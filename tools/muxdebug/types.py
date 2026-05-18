"""Data types and enums for mux-debug tool"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class TestResult(Enum):
    """Test result status"""

    SUCCESS = "✓"
    FAILURE = "✗"
    WARNING = "⚠"
    SKIP = "-"
    INFO = "ℹ"


class P2PProtocol(Enum):
    """P2P protocol type"""

    DEVP2P = "devp2p"
    LIBP2P = "libp2p"
    UNKNOWN = "unknown"


@dataclass
class PodInfo:
    """Pod information"""

    name: str
    namespace: str
    ip: str
    node: Optional[str] = None
    ready: bool = False


@dataclass
class PortRoute:
    """Port routing information"""

    mux_port: int
    protocol: str
    channel_name: str
    channel_namespace: str
    channel_port: int
    node_port: int
    target_pods: List[PodInfo] = field(default_factory=list)
    port_hash: Optional[str] = None
    channel_external_dns: Optional[str] = None


@dataclass
class MuxInfo:
    """Multiplexer service information"""

    name: str
    namespace: str
    lb_hostname: Optional[str]
    lb_ip: Optional[str]
    routes: List[PortRoute] = field(default_factory=list)
    total_channels: int = 0
    external_dns: Optional[str] = None


@dataclass
class PeerInfo:
    """Peer information for P2P protocols"""

    pod_name: str
    namespace: str
    protocol: P2PProtocol
    enode: Optional[str] = None
    peer_id: Optional[str] = None
    multiaddr: Optional[str] = None
    listen_port: Optional[int] = None
