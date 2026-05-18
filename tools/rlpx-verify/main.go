package main

import (
	"crypto/ecdsa"
	"encoding/json"
	"flag"
	"fmt"
	"net"
	"os"
	"time"

	"github.com/ethereum/go-ethereum/crypto"
	"github.com/ethereum/go-ethereum/p2p/rlpx"
)

// Result represents the verification result
type Result struct {
	Success    bool   `json:"success"`
	RemoteID   string `json:"remote_id,omitempty"`
	Error      string `json:"error,omitempty"`
	Duration   int64  `json:"duration_ms"`
	Verified   bool   `json:"verified"`
	ExpectedID string `json:"expected_id,omitempty"`
}

func main() {
	var (
		enodeURL   string
		timeout    int
		expectedID string
	)

	flag.StringVar(&enodeURL, "enode", "", "Enode URL to verify (enode://pubkey@host:port)")
	flag.IntVar(&timeout, "timeout", 5, "Connection timeout in seconds")
	flag.StringVar(&expectedID, "expected-id", "", "Expected peer ID (hex, without 0x prefix)")
	flag.Parse()

	if enodeURL == "" {
		result := Result{
			Success: false,
			Error:   "enode URL is required",
		}
		outputJSON(result)
		os.Exit(1)
	}

	result := verifyRLPx(enodeURL, time.Duration(timeout)*time.Second, expectedID)
	outputJSON(result)

	if !result.Success {
		os.Exit(1)
	}
}

func verifyRLPx(enodeURL string, timeout time.Duration, expectedID string) Result {
	start := time.Now()

	// Parse enode URL
	nodeID, addr, err := parseEnode(enodeURL)
	if err != nil {
		return Result{
			Success:  false,
			Error:    fmt.Sprintf("Failed to parse enode: %v", err),
			Duration: time.Since(start).Milliseconds(),
		}
	}

	// Connect to the node
	conn, err := net.DialTimeout("tcp", addr, timeout)
	if err != nil {
		return Result{
			Success:  false,
			Error:    fmt.Sprintf("TCP connection failed: %v", err),
			Duration: time.Since(start).Milliseconds(),
		}
	}
	defer conn.Close()

	// Set deadline for the entire handshake
	conn.SetDeadline(time.Now().Add(timeout))

	// Generate our ephemeral key pair
	privKey, err := crypto.GenerateKey()
	if err != nil {
		return Result{
			Success:  false,
			Error:    fmt.Sprintf("Failed to generate key: %v", err),
			Duration: time.Since(start).Milliseconds(),
		}
	}

	// Perform RLPx handshake
	remotePubKey, err := doHandshake(conn, privKey, nodeID)
	if err != nil {
		return Result{
			Success:  false,
			Error:    fmt.Sprintf("RLPx handshake failed: %v", err),
			Duration: time.Since(start).Milliseconds(),
		}
	}

	// Extract remote node ID from public key
	remoteID := fmt.Sprintf("%x", crypto.FromECDSAPub(remotePubKey)[1:]) // Skip 0x04 prefix

	// Verify if it matches expected ID
	verified := true
	if expectedID != "" && expectedID != remoteID {
		verified = false
	}

	return Result{
		Success:    true,
		RemoteID:   remoteID,
		Duration:   time.Since(start).Milliseconds(),
		Verified:   verified,
		ExpectedID: expectedID,
	}
}

func parseEnode(enodeURL string) ([]byte, string, error) {
	// Simple enode parser: enode://pubkey@host:port
	if len(enodeURL) < 10 || enodeURL[:8] != "enode://" {
		return nil, "", fmt.Errorf("invalid enode URL format")
	}

	// Find @ separator
	atIndex := -1
	for i := 8; i < len(enodeURL); i++ {
		if enodeURL[i] == '@' {
			atIndex = i
			break
		}
	}

	if atIndex == -1 {
		return nil, "", fmt.Errorf("invalid enode URL: missing @ separator")
	}

	// Extract pubkey hex (between enode:// and @)
	pubkeyHex := enodeURL[8:atIndex]
	if len(pubkeyHex) != 128 {
		return nil, "", fmt.Errorf("invalid pubkey length: expected 128 hex chars, got %d", len(pubkeyHex))
	}

	// Decode pubkey
	nodeID := make([]byte, 64)
	for i := 0; i < 64; i++ {
		var b byte
		fmt.Sscanf(pubkeyHex[i*2:i*2+2], "%02x", &b)
		nodeID[i] = b
	}

	// Extract host:port (after @ and before optional ?)
	addr := enodeURL[atIndex+1:]
	if qIndex := -1; qIndex != -1 {
		for i := 0; i < len(addr); i++ {
			if addr[i] == '?' {
				qIndex = i
				break
			}
		}
		if qIndex != -1 {
			addr = addr[:qIndex]
		}
	}

	return nodeID, addr, nil
}

func doHandshake(conn net.Conn, ourKey *ecdsa.PrivateKey, remoteNodeID []byte) (*ecdsa.PublicKey, error) {
	// Convert remote node ID to public key
	// RLPx expects 64-byte uncompressed key (without 0x04 prefix)
	if len(remoteNodeID) != 64 {
		return nil, fmt.Errorf("invalid remote node ID length: %d", len(remoteNodeID))
	}

	// Add 0x04 prefix for uncompressed public key
	pubKeyBytes := make([]byte, 65)
	pubKeyBytes[0] = 0x04
	copy(pubKeyBytes[1:], remoteNodeID)

	remotePub, err := crypto.UnmarshalPubkey(pubKeyBytes)
	if err != nil {
		return nil, fmt.Errorf("failed to unmarshal remote public key: %v", err)
	}

	// Create RLPx connection and perform handshake
	rlpxConn := rlpx.NewConn(conn, remotePub)

	// Perform handshake as initiator
	_, err = rlpxConn.Handshake(ourKey)
	if err != nil {
		return nil, fmt.Errorf("handshake failed: %v", err)
	}

	return remotePub, nil
}

func outputJSON(result Result) {
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(result); err != nil {
		fmt.Fprintf(os.Stderr, "Failed to encode JSON: %v\n", err)
		os.Exit(1)
	}
}
