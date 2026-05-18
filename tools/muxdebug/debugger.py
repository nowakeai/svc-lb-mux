"""Main debugger class for Service LoadBalancer Multiplexer"""

import json
import logging
import os
import socket
from typing import Dict, List, Optional, Tuple

from .types import TestResult, P2PProtocol, PodInfo, PortRoute, MuxInfo
from .k8s_client import KubeClient
from .p2p_tester import P2PTester

logger = logging.getLogger(__name__)

API_PREFIX = os.environ.get("API_PREFIX", "svc-mux.nowake.ai").strip() or "svc-mux.nowake.ai"


class MuxDebugger:
    """Main debugger class"""

    def __init__(self, context: Optional[str] = None):
        self.client = KubeClient(context)
        self.context = self.client.context
        self.p2p_tester = P2PTester(self.client)

    def list_mux_services(self) -> List[MuxInfo]:
        """List all multiplexer services"""
        logger.info(f"Listing mux services in context: {self.context}")

        services = self.client.list_resources("service")
        mux_list = []

        for svc in services:
            annotations = svc.get("metadata", {}).get("annotations", {})
            if annotations.get(f"{API_PREFIX}/multiplexer") == "true":
                mux_info = self._build_mux_info(svc)
                mux_list.append(mux_info)

        return mux_list

    def _build_mux_info(self, svc: Dict, include_routes: bool = False) -> MuxInfo:
        """Build MuxInfo from service data"""
        metadata = svc.get("metadata", {})
        status = svc.get("status", {})
        annotations = metadata.get("annotations", {})

        # Get LoadBalancer info
        lb_hostname = None
        lb_ip = None
        ingress_list = status.get("loadBalancer", {}).get("ingress", [])
        if ingress_list:
            ingress = ingress_list[0]
            lb_hostname = ingress.get("hostname")
            lb_ip = ingress.get("ip")

        # Parse channels
        channels_str = annotations.get(f"{API_PREFIX}/channels", "[]")
        try:
            channels = json.loads(channels_str)
        except json.JSONDecodeError:
            channels = []

        # Get external-dns annotation
        external_dns = annotations.get("external-dns.alpha.kubernetes.io/hostname")

        mux_info = MuxInfo(
            name=metadata["name"],
            namespace=metadata["namespace"],
            lb_hostname=lb_hostname,
            lb_ip=lb_ip,
            total_channels=len(channels),
            external_dns=external_dns,
        )

        if include_routes:
            mux_info.routes = self._build_routes(mux_info.name, mux_info.namespace)

        return mux_info

    def _build_routes(self, mux_name: str, mux_namespace: str) -> List[PortRoute]:
        """Build routing information for a mux"""
        routes = []

        # Find all channels using this mux
        services = self.client.list_resources("service")

        for svc in services:
            spec = svc.get("spec", {})
            metadata = svc.get("metadata", {})

            lb_class = spec.get("loadBalancerClass", "")
            if not lb_class.startswith(API_PREFIX + "/"):
                continue

            # Parse mux reference
            try:
                svc_mux_name, svc_mux_ns = self._parse_mux_from_lb_class(
                    lb_class, mux_namespace
                )
                if svc_mux_name != mux_name or svc_mux_ns != mux_namespace:
                    continue
            except ValueError:
                continue

            # Get channel info
            channel_name = metadata["name"]
            channel_namespace = metadata["namespace"]
            annotations = metadata.get("annotations", {})

            # Parse port mappings
            ports_anno = annotations.get(f"{API_PREFIX}/ports", "")

            # Get external-dns annotation for channel
            channel_external_dns = annotations.get("external-dns.alpha.kubernetes.io/hostname")

            # Get endpoints to find target pods
            endpoints = self.client.get_resource(
                "endpoints", channel_name, channel_namespace
            )
            pod_infos = self._extract_pod_infos(endpoints) if endpoints else []

            # Build routes for each port
            for port in spec.get("ports", []):
                node_port = port.get("nodePort")
                if not node_port:
                    continue

                route = PortRoute(
                    mux_port=node_port,  # In mux, the port is the nodePort
                    protocol=port["protocol"],
                    channel_name=channel_name,
                    channel_namespace=channel_namespace,
                    channel_port=port["port"],
                    node_port=node_port,
                    target_pods=pod_infos,
                    channel_external_dns=channel_external_dns,
                )

                # Try to find port hash from annotation
                if ports_anno:
                    for part in ports_anno.split(","):
                        if f"){node_port}:" in part:
                            route.port_hash = part.split(")")[0].strip("(")
                            break

                routes.append(route)

        return routes

    def _parse_mux_from_lb_class(
        self, lb_class: str, default_namespace: str
    ) -> Tuple[str, str]:
        """Parse mux name and namespace from loadBalancerClass"""
        prefix = API_PREFIX + "/"
        if not lb_class.startswith(prefix):
            raise ValueError(f"Invalid loadBalancerClass: {lb_class}")

        mux_part = lb_class[len(prefix) :]
        parts = mux_part.split(".")

        if len(parts) == 2:
            return parts[0], parts[1]
        else:
            return parts[0], default_namespace

    def _extract_pod_infos(self, endpoints: Dict) -> List[PodInfo]:
        """Extract pod information from endpoints"""
        pod_infos = []

        for subset in endpoints.get("subsets", []):
            # Get ready addresses
            for addr in subset.get("addresses", []):
                target_ref = addr.get("targetRef", {})
                if target_ref.get("kind") == "Pod":
                    pod_infos.append(
                        PodInfo(
                            name=target_ref["name"],
                            namespace=target_ref["namespace"],
                            ip=addr["ip"],
                            node=addr.get("nodeName"),
                            ready=True,
                        )
                    )

            # Get not ready addresses
            for addr in subset.get("notReadyAddresses", []):
                target_ref = addr.get("targetRef", {})
                if target_ref.get("kind") == "Pod":
                    pod_infos.append(
                        PodInfo(
                            name=target_ref["name"],
                            namespace=target_ref["namespace"],
                            ip=addr["ip"],
                            node=addr.get("nodeName"),
                            ready=False,
                        )
                    )

        return pod_infos

    def display_graph(self, mux_name: str, mux_namespace: str = "svc-mux"):
        """Display routing graph for a mux"""
        logger.info(f"Building routing graph for mux: {mux_namespace}/{mux_name}")

        mux_svc = self.client.get_resource("service", mux_name, mux_namespace)
        if not mux_svc:
            logger.error(f"Mux service not found: {mux_namespace}/{mux_name}")
            return

        mux_info = self._build_mux_info(mux_svc, include_routes=True)

        # Display header
        print(f"\n{'=' * 100}")
        print(f"Mux Routing Graph: {mux_namespace}/{mux_name}")
        print(f"{'=' * 100}")
        print(f"LoadBalancer: {mux_info.lb_hostname or mux_info.lb_ip or 'N/A'}")
        if mux_info.external_dns:
            print(f"External DNS: {mux_info.external_dns}")
        print(f"Total Channels: {mux_info.total_channels}")
        print(f"Total Routes: {len(mux_info.routes)}")
        print()

        if not mux_info.routes:
            print("No routes found.")
            return

        # Group routes by channel
        channels: Dict[Tuple[str, str], List[PortRoute]] = {}
        for route in mux_info.routes:
            key = (route.channel_namespace, route.channel_name)
            channels.setdefault(key, []).append(route)

        # Display each channel's routes
        for (ch_ns, ch_name), routes in sorted(channels.items()):
            print(f"┌─ Channel: {ch_ns}/{ch_name}")

            # Show channel external DNS if any
            if routes and routes[0].channel_external_dns:
                print(f"│  External DNS: {routes[0].channel_external_dns}")

            for i, route in enumerate(routes):
                is_last = i == len(routes) - 1
                prefix = "└──" if is_last else "├──"

                # Display route
                lb_addr = mux_info.lb_hostname or mux_info.lb_ip or "N/A"
                print(f"│  {prefix} {lb_addr}:{route.mux_port}/{route.protocol}")

                # Display arrow
                continuation = "   " if is_last else "│  "
                print(f"│  {continuation}    ↓")
                print(f"│  {continuation}    Service Port: {route.channel_port}")
                print(f"│  {continuation}    NodePort: {route.node_port}")

                # Display target pods
                if route.target_pods:
                    print(f"│  {continuation}    ↓")
                    print(f"│  {continuation}    Pods ({len(route.target_pods)}):")
                    for j, pod in enumerate(route.target_pods):
                        is_last_pod = j == len(route.target_pods) - 1
                        pod_prefix = "└──" if is_last_pod else "├──"
                        status_icon = (
                            TestResult.SUCCESS.value
                            if pod.ready
                            else TestResult.WARNING.value
                        )
                        print(
                            f"│  {continuation}      {pod_prefix} {status_icon} {pod.name}"
                        )
                        print(
                            f"│  {continuation}      {'   ' if is_last_pod else '│  '}   IP: {pod.ip}"
                        )
                        if pod.node:
                            print(
                                f"│  {continuation}      {'   ' if is_last_pod else '│  '}   Node: {pod.node}"
                            )
                else:
                    print(f"│  {continuation}    ↓")
                    print(
                        f"│  {continuation}    {TestResult.WARNING.value} No pods found"
                    )

                if not is_last:
                    print("│")

            print()

    def find_pod_route(
        self, pod_name: str, namespace: str
    ) -> Optional[Tuple[MuxInfo, PortRoute]]:
        """Find the route that points to a specific pod"""
        logger.info(f"Finding route for pod: {namespace}/{pod_name}")

        # Get all mux services
        mux_services = self.list_mux_services()

        for mux_info in mux_services:
            mux_with_routes = self._build_mux_info(
                self.client.get_resource("service", mux_info.name, mux_info.namespace),
                include_routes=True,
            )

            for route in mux_with_routes.routes:
                for pod in route.target_pods:
                    if pod.name == pod_name and pod.namespace == namespace:
                        return mux_with_routes, route

        return None

    def diagnose_route_failure(self, route: PortRoute, hostname: str) -> str:
        """Diagnose why a route test failed

        Returns detailed error message explaining the failure reason:
        - Pod not found
        - Pod not ready (with status details)
        - Port not defined in pod spec
        - Port not listening (need to check via kubectl exec)
        - Permission denied
        - Network connectivity issue
        """
        if not route.target_pods:
            return "No target pods found for this route"

        diagnoses = []

        for pod_info in route.target_pods:
            try:
                # Check if pod exists
                pod = self.client.get_resource("pod", pod_info.name, pod_info.namespace)

                if not pod:
                    diagnoses.append(f"Pod not found: {pod_info.namespace}/{pod_info.name}")
                    continue

                # Check pod status
                status = pod.get("status", {})
                phase = status.get("phase", "Unknown")

                if phase != "Running":
                    diagnoses.append(
                        f"Pod not ready: {pod_info.namespace}/{pod_info.name} is in {phase} phase"
                    )
                    continue

                # Check container statuses
                container_statuses = status.get("containerStatuses", [])
                not_ready_containers = []
                for container in container_statuses:
                    if not container.get("ready", False):
                        container_name = container.get("name", "unknown")
                        state = container.get("state", {})
                        state_str = ", ".join(f"{k}: {v}" for k, v in state.items())
                        not_ready_containers.append(f"{container_name} ({state_str})")

                if not_ready_containers:
                    diagnoses.append(
                        f"Pod has unready containers: {pod_info.namespace}/{pod_info.name} - {', '.join(not_ready_containers)}"
                    )
                    continue

                # Check if port is defined in pod spec
                pod_spec = pod.get("spec", {})
                containers = pod_spec.get("containers", [])
                port_found_in_spec = False

                for container in containers:
                    ports = container.get("ports", [])
                    for port in ports:
                        if port.get("containerPort") == route.channel_port:
                            port_found_in_spec = True
                            break
                    if port_found_in_spec:
                        break

                if not port_found_in_spec:
                    diagnoses.append(
                        f"Port {route.channel_port} not defined in pod spec: {pod_info.namespace}/{pod_info.name}"
                    )

                # Try to check if port is listening
                # Try multiple commands as different images may have different tools available
                port_check_commands = [
                    ["sh", "-c", f"netstat -tln 2>/dev/null | grep ':{route.channel_port} ' || ss -tln 2>/dev/null | grep ':{route.channel_port} '"],
                    ["sh", "-c", f"ss -tln | grep ':{route.channel_port} '"],
                    ["sh", "-c", f"netstat -tln | grep ':{route.channel_port} '"],
                ]

                port_listening = False
                permission_denied = False

                for cmd in port_check_commands:
                    result = self.client.exec_pod(pod_info.name, pod_info.namespace, cmd)

                    if result.returncode == 0 and result.stdout.strip():
                        port_listening = True
                        break
                    elif "permission denied" in result.stderr.lower() or "not permitted" in result.stderr.lower():
                        permission_denied = True

                if permission_denied:
                    diagnoses.append(
                        f"Permission denied when checking port: {pod_info.namespace}/{pod_info.name}"
                    )
                elif not port_listening:
                    diagnoses.append(
                        f"Port {route.channel_port} not listening in pod: {pod_info.namespace}/{pod_info.name}"
                    )

            except Exception as e:
                diagnoses.append(
                    f"Error diagnosing pod {pod_info.namespace}/{pod_info.name}: {str(e)}"
                )

        if not diagnoses:
            return "Network connectivity issue: TCP connection failed but pods appear healthy"

        return "; ".join(diagnoses)

    def test_tcp_connection(
        self, hostname: str, port: int, timeout: float = 5.0
    ) -> Tuple[TestResult, str]:
        """Test TCP connection"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((hostname, port))
            sock.close()

            if result == 0:
                return TestResult.SUCCESS, "TCP connection successful"
            else:
                return (
                    TestResult.FAILURE,
                    f"TCP connection failed (error code: {result})",
                )
        except socket.timeout:
            return TestResult.FAILURE, "Connection timeout"
        except socket.gaierror as e:
            return TestResult.FAILURE, f"DNS resolution failed: {e}"
        except Exception as e:
            return TestResult.FAILURE, f"Connection error: {e}"

    def _get_primary_external_host(self, external_dns: Optional[str], lb_hostname: Optional[str], lb_ip: Optional[str]) -> Optional[str]:
        """Get the primary external hostname for peer connections

        Prefers channel external-dns, then mux external-dns, then LB hostname.
        If external-dns contains multiple comma-separated entries, uses the first one.
        """
        if external_dns:
            # Split comma-separated DNS entries and take the first one
            dns_entries = [e.strip() for e in external_dns.split(",")]
            return dns_entries[0] if dns_entries else None
        return lb_hostname or lb_ip

    def test_pod(self, pod_name: str, namespace: str):
        """Test a specific pod"""
        logger.info(f"Testing pod: {namespace}/{pod_name}")

        # Find the route
        result = self.find_pod_route(pod_name, namespace)
        if not result:
            print(
                f"\n{TestResult.FAILURE.value} No route found for pod: {namespace}/{pod_name}"
            )
            return

        mux_info, route = result

        # Display info
        print(f"\n{'=' * 100}")
        print(f"Pod Test: {namespace}/{pod_name}")
        print(f"{'=' * 100}")
        print(f"Mux: {mux_info.namespace}/{mux_info.name}")
        if mux_info.external_dns:
            print(f"Mux External DNS: {mux_info.external_dns}")
        print(f"Channel: {route.channel_namespace}/{route.channel_name}")
        if route.channel_external_dns:
            print(f"Channel External DNS: {route.channel_external_dns}")
        print(
            f"Route: {mux_info.lb_hostname or mux_info.lb_ip}:{route.mux_port}/{route.protocol} -> {route.channel_port}"
        )
        print()

        # Test TCP connection
        if mux_info.lb_hostname or mux_info.lb_ip:
            hostname = mux_info.lb_hostname or mux_info.lb_ip
            result_tcp, msg_tcp = self.test_tcp_connection(hostname, route.mux_port)
            print(f"{result_tcp.value} TCP: {msg_tcp} to {hostname}:{route.mux_port}")
        else:
            print(f"{TestResult.SKIP.value} TCP: No LoadBalancer address available")

        # Test P2P connection
        protocol = self.p2p_tester.detect_p2p_protocol(pod_name)
        if protocol != P2PProtocol.UNKNOWN:
            print()
            print(f"P2P Protocol: {protocol.value}")

            peer_info = self.p2p_tester.get_peer_info(pod_name, namespace)
            if peer_info:
                if peer_info.enode:
                    print(f"Enode: {peer_info.enode}")
                    # Get primary external host (handles comma-separated DNS entries)
                    external_host = self._get_primary_external_host(
                        route.channel_external_dns or mux_info.external_dns,
                        mux_info.lb_hostname,
                        mux_info.lb_ip
                    )
                    if external_host:
                        external_enode = f"enode://{peer_info.peer_id}@{external_host}:{route.mux_port}"
                        print(f"\n{TestResult.INFO.value} External Enode:")
                        print(f"  {external_enode}")

                if peer_info.peer_id and protocol == P2PProtocol.LIBP2P:
                    print(f"Peer ID: {peer_info.peer_id}")
                    if peer_info.multiaddr:
                        print(f"Multiaddr: {peer_info.multiaddr}")
                    # Get primary external host (handles comma-separated DNS entries)
                    external_host = self._get_primary_external_host(
                        route.channel_external_dns or mux_info.external_dns,
                        mux_info.lb_hostname,
                        mux_info.lb_ip
                    )
                    if external_host:
                        external_multiaddr = f"/dns4/{external_host}/tcp/{route.mux_port}/p2p/{peer_info.peer_id}"
                        print(f"\n{TestResult.INFO.value} External Multiaddr:")
                        print(f"  {external_multiaddr}")

                # Test P2P protocol handshake
                print()
                if mux_info.lb_hostname or mux_info.lb_ip:
                    hostname = mux_info.lb_hostname or mux_info.lb_ip
                    if protocol == P2PProtocol.DEVP2P:
                        result_p2p, msg_p2p = self.p2p_tester.test_devp2p_handshake(
                            hostname, route.mux_port, peer_info.peer_id
                        )
                        print(f"{result_p2p.value} P2P: {msg_p2p}")
                    elif protocol == P2PProtocol.LIBP2P:
                        result_p2p, msg_p2p = self.p2p_tester.test_libp2p_handshake(
                            hostname, route.mux_port, peer_info.peer_id
                        )
                        print(f"{result_p2p.value} P2P: {msg_p2p}")
            else:
                print(f"{TestResult.WARNING.value} Could not retrieve peer info")

    def test_mux(self, mux_name: str, mux_namespace: str = "svc-mux"):
        """Test all routes in a mux"""
        logger.info(f"Testing mux: {mux_namespace}/{mux_name}")

        mux_svc = self.client.get_resource("service", mux_name, mux_namespace)
        if not mux_svc:
            logger.error(f"Mux service not found: {mux_namespace}/{mux_name}")
            return

        mux_info = self._build_mux_info(mux_svc, include_routes=True)

        print(f"\n{'=' * 100}")
        print(f"Mux Test: {mux_namespace}/{mux_name}")
        print(f"{'=' * 100}")
        print(f"LoadBalancer: {mux_info.lb_hostname or mux_info.lb_ip or 'N/A'}")
        if mux_info.external_dns:
            print(f"External DNS: {mux_info.external_dns}")
        print(f"Total Routes: {len(mux_info.routes)}")
        print()

        if not mux_info.routes:
            print("No routes found.")
            return

        if not (mux_info.lb_hostname or mux_info.lb_ip):
            print(
                f"{TestResult.SKIP.value} Cannot test: LoadBalancer address not available"
            )
            return

        hostname = mux_info.lb_hostname or mux_info.lb_ip

        # Group by channel
        channels: Dict[Tuple[str, str], List[PortRoute]] = {}
        for route in mux_info.routes:
            key = (route.channel_namespace, route.channel_name)
            channels.setdefault(key, []).append(route)

        # Test each route
        for (ch_ns, ch_name), routes in sorted(channels.items()):
            print(f"\nChannel: {ch_ns}/{ch_name}")
            print("-" * 80)

            for route in routes:
                result, msg = self.test_tcp_connection(
                    hostname, route.mux_port, timeout=3.0
                )
                print(f"{result.value} Port {route.mux_port}/{route.protocol}: {msg}")

                # Show pod count
                ready_pods = sum(1 for p in route.target_pods if p.ready)
                total_pods = len(route.target_pods)
                if total_pods > 0:
                    print(f"   Pods: {ready_pods}/{total_pods} ready")

                # Diagnose failure if test failed
                if result == TestResult.FAILURE:
                    diagnosis = self.diagnose_route_failure(route, hostname)
                    print(f"   Reason: {diagnosis}")

                # Try P2P protocol test if TCP succeeded and we have pods
                if result == TestResult.SUCCESS and route.target_pods:
                    # Get first ready pod for peer info
                    ready_pod = next((p for p in route.target_pods if p.ready), None)
                    if ready_pod:
                        peer_info = self.p2p_tester.get_peer_info(
                            ready_pod.name, ready_pod.namespace
                        )
                        if peer_info and peer_info.protocol != P2PProtocol.UNKNOWN:
                            # Test P2P handshake
                            peer_id = None
                            if peer_info.protocol == P2PProtocol.DEVP2P and peer_info.enode:
                                # Extract peer ID from enode URL
                                if peer_info.enode.startswith("enode://"):
                                    peer_id = peer_info.enode.split("@")[0].replace("enode://", "")
                            elif peer_info.protocol == P2PProtocol.LIBP2P:
                                peer_id = peer_info.peer_id

                            if peer_id:
                                if peer_info.protocol == P2PProtocol.DEVP2P:
                                    p2p_result, p2p_msg = self.p2p_tester.test_devp2p_handshake(
                                        hostname, route.mux_port, peer_id, timeout=5.0
                                    )
                                else:  # LIBP2P
                                    p2p_result, p2p_msg = self.p2p_tester.test_libp2p_handshake(
                                        hostname, route.mux_port, peer_id, timeout=5.0
                                    )
                                print(f"   {p2p_result.value} P2P ({peer_info.protocol.value}): {p2p_msg}")

    def test_all_muxes(self):
        """Test all mux services in current context"""
        logger.info("Testing all mux services")

        mux_list = self.list_mux_services()

        if not mux_list:
            print("No mux services found in current context")
            return

        print(f"\n{'=' * 100}")
        print(f"Testing All Mux Services (context: {self.context})")
        print(f"{'=' * 100}")
        print(f"Found {len(mux_list)} mux services\n")

        for mux in mux_list:
            print(f"\n{'#' * 100}")
            print(f"# Mux: {mux.namespace}/{mux.name}")
            print(f"{'#' * 100}\n")

            self.test_mux(mux.name, mux.namespace)

        # Print summary
        print(f"\n{'=' * 100}")
        print("Test Summary")
        print(f"{'=' * 100}")
        print(f"Total mux services tested: {len(mux_list)}")
