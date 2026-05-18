# RLPx Verification Tool

A lightweight Go tool for verifying Ethereum RLPx protocol handshakes using the official `go-ethereum` library.

## Purpose

This tool provides complete RLPx handshake verification for the `mux-debug` Python script. It's an optional dependency that enables full P2P protocol validation.

## Features

- ✅ Complete RLPx authentication handshake using `go-ethereum/p2p/rlpx`
- ✅ ECIES encryption/decryption
- ✅ Peer ID verification
- ✅ JSON output for easy integration
- ✅ Configurable timeout
- ✅ Minimal dependencies (only go-ethereum)

## Building

### Prerequisites

- Go 1.21 or later

### Build

```bash
# Download dependencies
make deps

# Build binary
make build

# Or build optimized release binary
make build-release

# Install to parent tools directory
make install
```

## Usage

### Command Line

```bash
# Basic usage
./rlpx-verify -enode "enode://pubkey@host:port"

# With timeout
./rlpx-verify -enode "enode://pubkey@host:port" -timeout 10

# Verify expected peer ID
./rlpx-verify -enode "enode://pubkey@host:port" -expected-id "pubkey"
```

### Output Format

JSON output on success:

```json
{
  "success": true,
  "remote_id": "abc123...",
  "duration_ms": 234,
  "verified": true,
  "expected_id": "abc123..."
}
```

JSON output on failure:

```json
{
  "success": false,
  "error": "TCP connection failed: connection refused",
  "duration_ms": 1002,
  "verified": false
}
```

### Exit Codes

- `0`: Success
- `1`: Failure (connection error, handshake failed, etc.)

## Integration with mux-debug

The `mux-debug` Python script automatically detects this tool:

1. If `rlpx-verify` is found in the same directory, `mux-debug` will use it for full RLPx verification
2. If not found, `mux-debug` falls back to basic TCP + RPC testing

No configuration needed - just build and the Python script will pick it up.

## Development

```bash
# Format code
go fmt ./...

# Run tests
go test ./...

# Update dependencies
make tidy
```

## Why Go?

- **Native geth support**: Direct access to `go-ethereum/p2p/rlpx` package
- **Proper ECIES**: Full encryption/decryption implementation
- **Performance**: Fast handshakes with minimal overhead
- **Small binary**: ~10MB compiled binary, no runtime dependencies

## Alternative: Pure Python

Implementing full RLPx in Python would require:
- `cryptography` for ECIES
- `eth-keys` for key handling
- Manual implementation of RLPx handshake protocol
- ~200+ lines of complex cryptographic code

Using Go + official geth library is more reliable and maintainable.

## References

- [Ethereum RLPx Specification](https://github.com/ethereum/devp2p/blob/master/rlpx.md)
- [go-ethereum p2p/rlpx](https://github.com/ethereum/go-ethereum/tree/master/p2p/rlpx)
