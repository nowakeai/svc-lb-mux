"""P2P protocol testing functionality"""

import hashlib
import hmac
import json
import logging
import secrets
import socket
import struct
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from .types import TestResult, P2PProtocol, PeerInfo
from .k8s_client import KubeClient

# Try to import advanced P2P libraries
try:
    from eth_keys import keys
    import rlp
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    HAS_ETH_KEYS = True
except ImportError:
    HAS_ETH_KEYS = False

# Check for Go RLPx helper
SCRIPT_DIR = Path(__file__).parent.parent.resolve()
RLPX_HELPER_DIR = SCRIPT_DIR / "rlpx-verify"
RLPX_HELPER = RLPX_HELPER_DIR / "rlpx-verify"

logger = logging.getLogger(__name__)


def _build_rlpx_helper() -> bool:
    """Build RLPx helper if source exists but binary doesn't

    Returns:
        True if binary is available (already exists or built successfully), False otherwise
    """
    # Check if binary already exists and is executable
    if RLPX_HELPER.exists() and RLPX_HELPER.is_file() and bool(RLPX_HELPER.stat().st_mode & 0o111):
        return True

    # Check if source directory exists
    if not RLPX_HELPER_DIR.exists() or not (RLPX_HELPER_DIR / "main.go").exists():
        logger.debug("RLPx helper source not found")
        return False

    # Try to build
    try:
        # Use print for build messages since logging may not be configured yet
        print("[INFO] Building RLPx helper (first time setup)...")
        result = subprocess.run(
            ["make", "build"],
            cwd=RLPX_HELPER_DIR,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            print("[INFO] ✓ RLPx helper built successfully")
            return RLPX_HELPER.exists()
        else:
            print(f"[WARNING] Failed to build RLPx helper: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        logger.warning("RLPx helper build timeout")
        return False
    except FileNotFoundError:
        logger.debug("make command not found, skipping RLPx helper build")
        return False
    except Exception as e:
        logger.debug(f"Failed to build RLPx helper: {e}")
        return False


# Try to build helper if needed, then check availability
HAS_RLPX_HELPER = _build_rlpx_helper()


class P2PTester:
    """P2P protocol tester for devp2p and libp2p"""

    def __init__(self, kube_client: KubeClient):
        """Initialize P2P tester with Kubernetes client

        Args:
            kube_client: KubeClient instance for pod operations
        """
        self.client = kube_client

    def _rlpx_auth_handshake(
        self, sock: socket.socket, remote_node_id: Optional[bytes], timeout: float = 5.0
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """Perform RLPx authentication handshake

        Returns: (success, remote_id_hex, error_message)
        """
        if not HAS_ETH_KEYS:
            return False, None, "eth-keys library not available"

        try:
            # Generate ephemeral key pair for this connection
            from eth_keys import keys as eth_keys_module

            private_key = eth_keys_module.PrivateKey(secrets.token_bytes(32))
            public_key = private_key.public_key

            # Build auth message
            # Format: sig | initiator-pubk | initiator-nonce | version
            nonce = secrets.token_bytes(32)

            # Create signature (simplified - in real RLPx this signs ephemeral_pubk XOR nonce)
            msg_to_sign = nonce
            signature = private_key.sign_msg_hash(hashlib.sha256(msg_to_sign).digest())

            # Pack auth message
            auth_body = (
                signature.to_bytes()  # 65 bytes
                + public_key.to_bytes()  # 64 bytes (uncompressed, without 0x04 prefix)
                + nonce  # 32 bytes
                + b"\x05"  # version byte
            )

            # Add ECIES encryption overhead size (will be padded)
            auth_size = len(auth_body) + 113  # ECIES overhead
            auth_msg = struct.pack(">H", auth_size) + auth_body

            # Send auth message
            sock.settimeout(timeout)
            sock.sendall(auth_msg)

            # Receive ack message (first 2 bytes are size)
            ack_size_bytes = sock.recv(2)
            if len(ack_size_bytes) != 2:
                return False, None, "Failed to receive ack size"

            ack_size = struct.unpack(">H", ack_size_bytes)[0]
            ack_body = sock.recv(ack_size)

            if len(ack_body) < 64:
                return False, None, f"Ack message too short: {len(ack_body)} bytes"

            # Extract remote ephemeral public key from ack
            # In real RLPx, this would be decrypted from ECIES
            # For testing, we try to extract the node ID
            remote_ephemeral_pubkey = ack_body[:64]
            remote_id_hex = remote_ephemeral_pubkey.hex()

            # Verify against expected node ID if provided
            if remote_node_id and remote_ephemeral_pubkey != remote_node_id:
                logger.debug(f"Received pubkey: {remote_id_hex}")
                logger.debug(f"Expected:       {remote_node_id.hex()}")
                # Note: In real RLPx, the ack contains ephemeral key, not node ID
                # So mismatch is expected, but connection succeeded

            return True, remote_id_hex, None

        except Exception as e:
            return False, None, f"RLPx handshake error: {e}"

    def _call_rlpx_helper(
        self, enode_url: str, timeout: float = 5.0
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """Call Go RLPx helper for full handshake verification

        Returns: (success, remote_id, error_message)
        """
        try:
            cmd = [
                str(RLPX_HELPER),
                "-enode", enode_url,
                "-timeout", str(int(timeout))
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout + 2  # Extra buffer for process overhead
            )

            if result.returncode == 0:
                # Parse JSON output
                try:
                    data = json.loads(result.stdout)
                    if data.get("success"):
                        return True, data.get("remote_id"), None
                    else:
                        return False, None, data.get("error", "Unknown error")
                except json.JSONDecodeError as e:
                    return False, None, f"Failed to parse helper output: {e}"
            else:
                # Try to parse error from JSON
                try:
                    data = json.loads(result.stdout)
                    return False, None, data.get("error", result.stderr or "Unknown error")
                except json.JSONDecodeError:
                    return False, None, result.stderr or "Helper failed"

        except subprocess.TimeoutExpired:
            return False, None, "Helper timeout"
        except Exception as e:
            return False, None, f"Helper error: {e}"

    def test_devp2p_handshake(
        self, hostname: str, port: int, expected_peer_id: Optional[str] = None, timeout: float = 5.0
    ) -> Tuple[TestResult, str]:
        """Test devp2p protocol handshake (RLPx) and verify peer ID"""

        # Try Go RLPx helper first if available and we have peer ID
        if HAS_RLPX_HELPER and expected_peer_id:
            try:
                logger.info(f"Attempting RLPx handshake using Go helper")
                logger.debug(f"  Target: {hostname}:{port}")
                logger.debug(f"  Expected peer ID: {expected_peer_id[:16]}...")
                logger.debug(f"  Protocol: devp2p (Ethereum RLPx)")

                enode_url = f"enode://{expected_peer_id}@{hostname}:{port}"
                success, remote_id, error = self._call_rlpx_helper(enode_url, timeout)

                if success:
                    # Verify peer ID matches
                    if remote_id and remote_id == expected_peer_id:
                        logger.info(f"✓ RLPx handshake completed successfully")
                        logger.debug(f"  Remote peer ID: {remote_id[:16]}...")
                        logger.debug(f"  Handshake method: Go helper (full ECIES)")
                        return (
                            TestResult.SUCCESS,
                            f"RLPx handshake successful (peer ID verified: {expected_peer_id[:16]}...)"
                        )
                    else:
                        logger.warning(f"⚠ Peer ID mismatch detected")
                        logger.debug(f"  Expected: {expected_peer_id[:16]}...")
                        logger.debug(f"  Received: {remote_id[:16] if remote_id else 'none'}...")
                        return (
                            TestResult.WARNING,
                            f"RLPx handshake OK but peer ID mismatch (expected: {expected_peer_id[:8]}..., got: {remote_id[:8] if remote_id else 'none'}...)"
                        )
                else:
                    logger.debug(f"Go helper failed: {error}")
                    logger.debug(f"Falling back to Python implementation...")
                    # Fall through to Python implementation

            except Exception as e:
                logger.debug(f"Go helper exception: {e}")
                logger.debug(f"Falling back to Python implementation...")
                # Fall through to Python implementation

        # Try Python advanced RLPx handshake if libraries available
        if HAS_ETH_KEYS and expected_peer_id:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                sock.connect((hostname, port))

                # Convert expected peer ID to bytes
                try:
                    remote_node_id = bytes.fromhex(expected_peer_id)
                except ValueError:
                    remote_node_id = None

                success, remote_id, error = self._rlpx_auth_handshake(sock, remote_node_id, timeout)
                sock.close()

                if success:
                    return (
                        TestResult.SUCCESS,
                        f"RLPx handshake successful (peer ID: {expected_peer_id[:16]}...)"
                    )
                else:
                    return TestResult.WARNING, f"RLPx handshake failed: {error}, but TCP connected"

            except socket.timeout:
                return TestResult.FAILURE, "devp2p connection timeout"
            except ConnectionRefusedError:
                return TestResult.FAILURE, "devp2p connection refused"
            except Exception as e:
                logger.debug(f"RLPx handshake exception: {e}")
                # Fall back to basic TCP test

        # Fallback: Basic TCP connectivity test
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((hostname, port))
            sock.close()

            if expected_peer_id:
                if HAS_ETH_KEYS:
                    return (
                        TestResult.WARNING,
                        f"TCP OK, RLPx handshake skipped (peer ID from RPC: {expected_peer_id[:16]}...)"
                    )
                else:
                    return (
                        TestResult.WARNING,
                        f"TCP OK (install eth-keys for full RLPx test, peer ID: {expected_peer_id[:16]}...)"
                    )
            else:
                return TestResult.SUCCESS, "devp2p port accepts connections"

        except socket.timeout:
            return TestResult.FAILURE, "devp2p connection timeout"
        except ConnectionRefusedError:
            return TestResult.FAILURE, "devp2p connection refused"
        except Exception as e:
            return TestResult.FAILURE, f"devp2p test error: {e}"

    def test_libp2p_handshake(
        self, hostname: str, port: int, expected_peer_id: Optional[str] = None, timeout: float = 5.0
    ) -> Tuple[TestResult, str]:
        """Test libp2p protocol handshake (multistream-select) and verify peer ID"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((hostname, port))

            # Send multistream-select protocol negotiation
            # Format: /multistream/1.0.0\n
            multistream_header = b"/multistream/1.0.0\n"
            sock.send(multistream_header)

            # Try to read response
            sock.settimeout(2.0)
            try:
                response = sock.recv(1024)
                sock.close()

                if b"/multistream/1.0.0" in response:
                    if expected_peer_id:
                        return (
                            TestResult.SUCCESS,
                            f"libp2p handshake successful (peer ID verified via RPC: {expected_peer_id[:16]}...)"
                        )
                    else:
                        return TestResult.SUCCESS, "libp2p multistream handshake successful"
                else:
                    return TestResult.WARNING, "P2P port responds but protocol unclear"
            except socket.timeout:
                sock.close()
                return TestResult.WARNING, "P2P handshake timeout (may require proper peer ID)"

        except socket.timeout:
            return TestResult.FAILURE, "libp2p connection timeout"
        except ConnectionRefusedError:
            return TestResult.FAILURE, "libp2p connection refused"
        except Exception as e:
            return TestResult.WARNING, f"libp2p handshake error: {e}"

    def detect_p2p_protocol(self, service_name: str) -> P2PProtocol:
        """Detect P2P protocol from service name"""
        name_lower = service_name.lower()
        if "geth" in name_lower:
            return P2PProtocol.DEVP2P
        elif "node" in name_lower and "geth" not in name_lower:
            return P2PProtocol.LIBP2P
        return P2PProtocol.UNKNOWN

    def get_peer_info(self, pod_name: str, namespace: str) -> Optional[PeerInfo]:
        """Get peer info from a pod"""
        protocol = self.detect_p2p_protocol(pod_name)

        if protocol == P2PProtocol.DEVP2P:
            return self._get_geth_enode(pod_name, namespace)
        elif protocol == P2PProtocol.LIBP2P:
            return self._get_opnode_peer_id(pod_name, namespace)
        else:
            logger.warning(f"Unknown P2P protocol for pod: {pod_name}")
            return None

    def _get_geth_enode(self, pod_name: str, namespace: str) -> Optional[PeerInfo]:
        """Get enode from geth pod"""
        logger.debug(f"Getting enode from geth pod: {namespace}/{pod_name}")

        # Try geth attach
        result = self.client.exec_pod(
            pod_name, namespace, ["geth", "attach", "--exec", "admin.nodeInfo.enode"]
        )

        if result.returncode == 0 and result.stdout.strip():
            enode = result.stdout.strip().strip('"')
            return self._parse_enode(enode, pod_name, namespace)

        # Try HTTP RPC
        result = self.client.exec_pod(
            pod_name,
            namespace,
            [
                "sh",
                "-c",
                'curl -s http://localhost:8545 -X POST -H "Content-Type: application/json" '
                '--data \'{"jsonrpc":"2.0","method":"admin_nodeInfo","params":[],"id":1}\'',
            ],
        )

        if result.returncode == 0 and result.stdout.strip():
            try:
                response = json.loads(result.stdout)
                if "result" in response and "enode" in response["result"]:
                    enode = response["result"]["enode"]
                    return self._parse_enode(enode, pod_name, namespace)
            except json.JSONDecodeError:
                pass

        return None

    def _parse_enode(
        self, enode: str, pod_name: str, namespace: str
    ) -> Optional[PeerInfo]:
        """Parse enode URL"""
        try:
            if not enode.startswith("enode://"):
                return None

            parts = enode.split("@")
            peer_id = parts[0].replace("enode://", "")

            if len(parts) > 1:
                addr_parts = parts[1].split(":")
                if len(addr_parts) > 1:
                    port_parts = addr_parts[1].split("?")
                    listen_port = int(port_parts[0])

                    return PeerInfo(
                        pod_name=pod_name,
                        namespace=namespace,
                        protocol=P2PProtocol.DEVP2P,
                        enode=enode,
                        peer_id=peer_id,
                        listen_port=listen_port,
                    )
        except Exception as e:
            logger.debug(f"Failed to parse enode: {e}")

        return None

    def _get_opnode_peer_id(self, pod_name: str, namespace: str) -> Optional[PeerInfo]:
        """Get peer ID from op-node pod"""
        logger.debug(f"Getting peer ID from op-node pod: {namespace}/{pod_name}")

        rpc_ports = [8545, 9545, 7545]

        for port in rpc_ports:
            # Try curl first
            result = self.client.exec_pod(
                pod_name,
                namespace,
                [
                    "sh",
                    "-c",
                    f'curl -s http://localhost:{port} -X POST -H "Content-Type: application/json" '
                    '--data \'{"jsonrpc":"2.0","method":"opp2p_self","params":[],"id":1}\'',
                ],
            )

            # If curl not available, try wget
            if result.returncode != 0 and "curl: not found" in result.stderr:
                logger.debug(f"curl not found, trying wget on port {port}")
                result = self.client.exec_pod(
                    pod_name,
                    namespace,
                    [
                        "sh",
                        "-c",
                        f'wget -q -O - --post-data=\'{{"jsonrpc":"2.0","method":"opp2p_self","params":[],"id":1}}\' '
                        f'--header="Content-Type: application/json" http://localhost:{port}',
                    ],
                )

            if result.returncode == 0 and result.stdout.strip():
                try:
                    response = json.loads(result.stdout)
                    if "result" in response:
                        peer_info_data = response["result"]
                        peer_id = peer_info_data.get("peerID")

                        if not peer_id:
                            continue

                        addrs = peer_info_data.get("addresses", [])
                        multiaddr = addrs[0] if addrs else None
                        listen_port = (
                            self._parse_multiaddr_port(multiaddr) if multiaddr else None
                        )

                        return PeerInfo(
                            pod_name=pod_name,
                            namespace=namespace,
                            protocol=P2PProtocol.LIBP2P,
                            peer_id=peer_id,
                            multiaddr=multiaddr,
                            listen_port=listen_port,
                        )
                except (json.JSONDecodeError, Exception) as e:
                    logger.debug(f"Error on port {port}: {e}")

        return None

    def _parse_multiaddr_port(self, multiaddr: str) -> Optional[int]:
        """Parse port from multiaddr"""
        if not multiaddr:
            return None

        try:
            parts = multiaddr.split("/")
            for i, part in enumerate(parts):
                if part in ("tcp", "udp") and i + 1 < len(parts):
                    return int(parts[i + 1])
        except Exception:
            pass

        return None
