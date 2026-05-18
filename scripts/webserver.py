"""Debug webserver for LB4 Multiplexer using aiohttp"""

import asyncio
import base64
import hmac
import logging
import os
import socket
import sys
import threading
from collections import deque
from datetime import datetime
from pathlib import Path

import aiohttp.web

sys.path.append(os.path.dirname(__file__))

try:
    import kopf
except ImportError:
    kopf = None  # For dryrun mode

logger = logging.getLogger(__name__)

# Dryrun mode flag - operator will be read-only, no write operations to k8s
DRYRUN_MODE = os.environ.get("DRYRUN_MODE", "").lower() in ("true", "1", "yes", "on")

API_PREFIX = os.environ.get("API_PREFIX", "svc-mux.nowake.ai").strip() or "svc-mux.nowake.ai"
LEGACY_API_PREFIXES = tuple(
    prefix.strip()
    for prefix in os.environ.get(
        "LEGACY_API_PREFIXES", "lb4-multiplexer.altlayer.io"
    ).split(",")
    if prefix.strip() and prefix.strip() != API_PREFIX
)
API_PREFIXES = (API_PREFIX, *LEGACY_API_PREFIXES)

# Authentication token from environment variable
# If set, all web UI requests must provide this token
AUTH_TOKEN = os.environ.get("DEBUG_WEB_AUTH_TOKEN", os.environ.get("AUTH_TOKEN", ""))
AUTH_ENABLED = bool(AUTH_TOKEN)

# Global state storage with thread-safe lock
_state_lock = threading.Lock()
_state = {
    "events": deque(maxlen=100),  # Recent events
    "mux_services": {},  # {(namespace, name): service_data}
    "channel_services": {},  # {(namespace, name): service_data}
    "endpoints": {},  # {(namespace, name): endpoints_data}
}


def check_auth(request):
    """Check if request has valid authentication using Basic Auth.

    Uses HTTP Basic Authentication where:
    - Username: can be anything (typically "admin" or just use the token)
    - Password: must match AUTH_TOKEN

    Args:
        request: aiohttp request object

    Returns:
        bool: True if authenticated or auth is disabled, False otherwise
    """
    if not AUTH_ENABLED:
        return True

    # Check Authorization header for Basic auth
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Basic "):
        try:
            # Decode base64 credentials
            credentials = base64.b64decode(auth_header[6:]).decode("utf-8")
            # Format is "username:password", we only care about password (the token)
            if ":" in credentials:
                username, password = credentials.split(":", 1)
                # Check if password matches the token
                # Username is ignored, can be anything
                if hmac.compare_digest(password, AUTH_TOKEN):
                    return True
        except Exception:
            pass

    return False


@aiohttp.web.middleware
async def auth_middleware(request, handler):
    """Middleware to check authentication for all requests except health check.

    Uses HTTP Basic Authentication. On failed authentication, adds a delay
    to prevent brute force attacks.
    """
    # Skip auth for health check endpoint
    if request.path == "/healthz":
        return await handler(request)

    # Check authentication
    is_authenticated = check_auth(request)

    if not is_authenticated:
        # Anti brute-force: sleep 2 seconds on failed authentication
        # This significantly slows down password guessing attacks
        await asyncio.sleep(2)

        return aiohttp.web.Response(
            text="Unauthorized - authentication required",
            status=401,
            headers={"WWW-Authenticate": 'Basic realm="LB4 Multiplexer Debug UI"'},
        )

    # Process the request
    return await handler(request)


def get_annotation(annotations: dict, name: str, default=None):
    for prefix in API_PREFIXES:
        key = f"{prefix}/{name}"
        if key in annotations:
            return annotations[key]
    return default


def record_event(event_type: str, resource: str, message: str):
    """Record an event for display in web UI"""
    with _state_lock:
        _state["events"].appendleft(
            {
                "timestamp": datetime.now().isoformat(),
                "type": event_type,
                "resource": resource,
                "message": message,
            }
        )


def delete_mux_state(mux_key):
    """Remove a deleted mux and its channels from state

    Args:
        mux_key: Tuple (namespace, name) of the mux to remove
    """
    with _state_lock:
        # Remove mux service
        if mux_key in _state["mux_services"]:
            logger.debug(f"Removing deleted mux {mux_key[0]}/{mux_key[1]} from state")
            del _state["mux_services"][mux_key]

        # Remove mux endpoints
        if mux_key in _state["endpoints"]:
            del _state["endpoints"][mux_key]

        # Remove all channels belonging to this mux
        channels_to_remove = [
            key
            for key, ch_data in _state["channel_services"].items()
            if (
                ch_data.get("mux_namespace") == mux_key[0]
                and ch_data.get("mux_name") == mux_key[1]
            )
        ]

        for key in channels_to_remove:
            logger.debug(
                f"Removing channel {key[0]}/{key[1]} of deleted mux {mux_key[0]}/{mux_key[1]}"
            )
            del _state["channel_services"][key]
            # Also remove channel endpoints
            if key in _state["endpoints"]:
                del _state["endpoints"][key]


def update_mux_state(
    memo=None,
    mux_channels=None,
    mux_key=None,
    mux_service_data=None,
):
    """Update internal state for a specific mux from kopf memo and indexes

    Args:
        memo: kopf memo containing mux_queues and endpoints
        mux_channels: Index of channels by mux
        mux_key: Tuple (namespace, name) of the specific mux to update
        mux_service_data: Service data for the specific mux (including status, annotations)
    """
    logger.debug(
        f"update_mux_state called for mux {mux_key[0]}/{mux_key[1]} "
        f"with service_data={'present' if mux_service_data else 'None'}"
    )

    with _state_lock:
        # Update specific mux service with external DNS info
        # Only update the specified mux, preserving other mux entries in state
        # Note: We update state even without queue (read-only mode for debugging)
        has_queue = mux_key in memo.mux_queues
        logger.debug(
            f"Processing mux {mux_key[0]}/{mux_key[1]}, key type={type(mux_key)}, key={mux_key!r}, has_queue={has_queue}"
        )
        mux_data = {
            "namespace": mux_key[0],
            "name": mux_key[1],
            "has_queue": has_queue,
            "external_dns_hostname": None,  # the hostname from external-dns annotation
            "status_ingress": None,  # the external IP or hostname from status.loadBalancer.ingress
        }

        # Get mux service data if available
        if mux_service_data:
            # Extract external DNS from annotations or status
            annotations = mux_service_data.get("metadata", {}).get(
                "annotations", {}
            )
            logger.debug(
                f"Mux {mux_key[0]}/{mux_key[1]} annotations keys: {list(annotations.keys())}"
            )
            external_dns = annotations.get(
                "external-dns.alpha.kubernetes.io/hostname"
            )
            logger.debug(
                f"Mux {mux_key[0]}/{mux_key[1]} external-dns annotation value: {external_dns}"
            )

            # Extract external IP and hostname from status
            status_lb = mux_service_data.get("status", {}).get("loadBalancer", {})
            ingress = status_lb.get("ingress", [])

            if ingress:
                mux_data["status_ingress"] = ingress[0].get("ip") or ingress[0].get(
                    "hostname"
                )
            # Priority: annotation > status hostname > external IP
            mux_data["external_dns_hostname"] = external_dns

            # Debug logging
            if external_dns:
                logger.debug(
                    f"Mux {mux_key[0]}/{mux_key[1]} external_dns: {external_dns}"
                )
            else:
                logger.warning(
                    f"Mux {mux_key[0]}/{mux_key[1]} has no external_dns in annotations"
                )

        _state["mux_services"][mux_key] = mux_data

        # Update mux endpoint information
        # Only update the endpoint for the current mux
        if mux_key in memo.endpoints:
            value = memo.endpoints[mux_key]
            pods = []
            # Extract pod info from endpoints (with namespace)
            for subset in value.get("subsets", []):
                for addr in subset.get("addresses", []):
                    if "ip" in addr:
                        target_ref = addr.get("targetRef", {})
                        if target_ref.get("kind") == "Pod":
                            pod_name = target_ref.get("name", addr["ip"])
                            pod_ns = target_ref.get("namespace", mux_key[0])
                            # Store as "namespace/podname" format
                            pods.append(f"{pod_ns}/{pod_name}")
                        else:
                            pods.append(addr["ip"])

            _state["endpoints"][mux_key] = {
                "namespace": mux_key[0],
                "name": mux_key[1],
                "ready_count": sum(
                    len(subset.get("addresses", []))
                    for subset in value.get("subsets", [])
                ),
                "not_ready_count": sum(
                    len(subset.get("notReadyAddresses", []))
                    for subset in value.get("subsets", [])
                ),
                "pods": pods,
            }

        # Update channel services for current mux only
        # Get channels belonging to this specific mux
        channel_set = mux_channels.get(mux_key, set())

        # Get mux external DNS and IP for fallback
        mux_svc_data = _state["mux_services"].get(mux_key, {})
        mux_external_dns_hostname = mux_svc_data.get("external_dns_hostname")
        mux_status_ingress = mux_svc_data.get("status_ingress")

        # Track current channels to identify removed ones
        current_channel_keys = set()

        for ch in channel_set:
            ch_ns = ch["metadata"]["namespace"]
            ch_name = ch["metadata"]["name"]
            ch_key = (ch_ns, ch_name)
            current_channel_keys.add(ch_key)

            # Update channel endpoints if available
            if ch_key in memo.endpoints:
                value = memo.endpoints[ch_key]
                ch_pods = []
                # Extract pod info from endpoints (with namespace)
                for subset in value.get("subsets", []):
                    for addr in subset.get("addresses", []):
                        if "ip" in addr:
                            target_ref = addr.get("targetRef", {})
                            if target_ref.get("kind") == "Pod":
                                pod_name = target_ref.get("name", addr["ip"])
                                pod_ns = target_ref.get("namespace", ch_ns)
                                # Store as "namespace/podname" format
                                ch_pods.append(f"{pod_ns}/{pod_name}")
                            else:
                                ch_pods.append(addr["ip"])

                _state["endpoints"][ch_key] = {
                    "namespace": ch_ns,
                    "name": ch_name,
                    "ready_count": sum(
                        len(subset.get("addresses", []))
                        for subset in value.get("subsets", [])
                    ),
                    "not_ready_count": sum(
                        len(subset.get("notReadyAddresses", []))
                        for subset in value.get("subsets", [])
                    ),
                    "pods": ch_pods,
                }

            lb_class = ch["spec"].get("loadBalancerClass", "")
            ports = ch["spec"].get("ports", [])

            # Get channel external DNS from annotations first
            annotations = ch.get("metadata", {}).get("annotations", {})
            ch_external_dns_annotation = annotations.get(
                "external-dns.alpha.kubernetes.io/hostname"
            )

            # Get channel status.loadBalancer.ingress
            loadbalancer_ingress = (
                ch.get("status", {}).get("loadBalancer", {}).get("ingress", [])
            )
            ch_status_ingress = None
            if loadbalancer_ingress:
                ch_status_ingress = loadbalancer_ingress[0].get(
                    "ip"
                ) or loadbalancer_ingress[0].get("hostname")

            # Determine final external DNS
            # Priority: channel annotation > mux annotation > channel status > mux status
            external_dns = (
                ch_external_dns_annotation
                or mux_external_dns_hostname
                or ch_status_ingress
                or mux_status_ingress
            )

            # Parse mux port mapping from annotations
            # Format: "port1:8080->30001,port2:8081->30002" or "port1:30001,port2:30002"
            # mapping channel port to mux nodePort
            mux_ports_anno = get_annotation(annotations, "ports", "")
            mux_port_map = {}
            if mux_ports_anno:
                for mapping in mux_ports_anno.split(","):
                    mapping = mapping.strip()
                    if ":" in mapping:
                        port_name, port_spec = mapping.split(":", 1)
                        # Handle format "8545->30001" or just "30001"
                        if "->" in port_spec:
                            _, mux_port = port_spec.split("->", 1)
                        else:
                            mux_port = port_spec
                        mux_port_map[port_name.strip()] = int(mux_port.strip())

            # Update channel service state for this specific channel
            _state["channel_services"][ch_key] = {
                "namespace": ch_ns,
                "name": ch_name,
                "mux_namespace": mux_key[0],
                "mux_name": mux_key[1],
                "lb_class": lb_class,
                "external_dns": external_dns,
                "custom_dns": ch_external_dns_annotation,  # Channel's own DNS annotation (if any)
                "ports": [
                    {
                        "name": p.get("name"),
                        "port": p.get("port"),
                        "node_port": p.get("nodePort"),
                        "protocol": p.get("protocol"),
                        "mux_port": mux_port_map.get(p.get("name")),
                    }
                    for p in ports
                ],
            }

        # Remove channels that no longer belong to this mux
        # Find all channels in state that belong to this mux but are not in current set
        channels_to_remove = [
            key
            for key, ch_data in _state["channel_services"].items()
            if (
                ch_data.get("mux_namespace") == mux_key[0]
                and ch_data.get("mux_name") == mux_key[1]
                and key not in current_channel_keys
            )
        ]

        for key in channels_to_remove:
            logger.debug(f"Removing deleted channel {key[0]}/{key[1]} from state")
            del _state["channel_services"][key]
            # Also remove channel endpoints
            if key in _state["endpoints"]:
                del _state["endpoints"][key]


# HTTP Handlers
async def handle_index(request):
    """Serve the main HTML page from file"""
    html_path = Path(__file__).parent / "index.html"
    return aiohttp.web.FileResponse(html_path)


async def handle_state(request):
    """Serve current state as JSON"""
    # Convert dict keys (tuples) to strings for JSON serialization.
    with _state_lock:
        state = {
            "mux_services": {
                f"{k[0]}/{k[1]}": dict(v)
                for k, v in _state["mux_services"].items()
            },
            "channel_services": {
                f"{k[0]}/{k[1]}": dict(v)
                for k, v in _state["channel_services"].items()
            },
            "endpoints": {
                f"{k[0]}/{k[1]}": dict(v)
                for k, v in _state["endpoints"].items()
            },
            "events": list(_state["events"]),
        }
    return aiohttp.web.json_response(state)


async def handle_topology(request):
    """Serve topology graph data with mux external DNS"""
    topology = {}
    with _state_lock:
        channel_services_snapshot = [
            (key, dict(value))
            for key, value in _state["channel_services"].items()
        ]
        mux_services_snapshot = {
            key: dict(value) for key, value in _state["mux_services"].items()
        }

    for ch_key, ch in channel_services_snapshot:
        mux_key = f"{ch['mux_namespace']}/{ch['mux_name']}"
        mux_tuple_key = (ch["mux_namespace"], ch["mux_name"])

        # Check if mux exists in state
        mux_exists = mux_tuple_key in mux_services_snapshot

        if mux_key not in topology:
            # Get mux external DNS from mux_services
            if mux_exists:
                mux_info = mux_services_snapshot[mux_tuple_key]
                topology[mux_key] = {
                    "mux_external_dns": mux_info.get("external_dns_hostname"),
                    "mux_external_ip": mux_info.get("status_ingress"),
                    "mux_missing": False,
                    "has_queue": mux_info.get("has_queue", False),
                    "channels": [],
                }
            else:
                # Mux doesn't exist - orphaned channel
                logger.warning(
                    f"Channel {ch['namespace']}/{ch['name']} references "
                    f"non-existent mux {ch['mux_namespace']}/{ch['mux_name']}"
                )
                topology[mux_key] = {
                    "mux_external_dns": None,
                    "mux_external_ip": None,
                    "mux_missing": True,  # Flag for frontend
                    "has_queue": False,
                    "channels": [],
                }

        topology[mux_key]["channels"].append(
            {
                "namespace": ch["namespace"],
                "name": ch["name"],
                "external_dns": ch["external_dns"],
                "custom_dns": ch.get("custom_dns"),  # Channel's own DNS if any
                "ports": ch["ports"],
            }
        )
    return aiohttp.web.json_response(topology)


async def handle_test_tcp(request):
    """Test TCP connection to a host:port"""
    host = request.query.get("host")
    port = request.query.get("port")
    # Optional: get resource identifier for event logging (format: "namespace/name")
    resource = request.query.get("resource", "unknown")

    if not host or not port:
        return aiohttp.web.json_response(
            {"success": False, "error": "Missing host or port"}, status=400
        )

    try:
        port = int(port)
    except ValueError:
        return aiohttp.web.json_response(
            {"success": False, "error": "Invalid port"}, status=400
        )
    if not 1 <= port <= 65535:
        return aiohttp.web.json_response(
            {"success": False, "error": "Port out of range"}, status=400
        )

    # Perform TCP connection test with timeout
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3.0)  # 3 second timeout
        result = sock.connect_ex((host, port))
        sock.close()

        if result == 0:
            # Success - record as Normal event
            record_event(
                "Normal",
                resource,
                f"ConnectionTest: Successfully connected to {host}:{port}",
            )
            return aiohttp.web.json_response(
                {"success": True, "message": f"Connected to {host}:{port}"}
            )
        else:
            # Connection failed - record as Warning event
            record_event(
                "Warning",
                resource,
                f"ConnectionTest: Connection failed to {host}:{port} (error code {result})",
            )
            return aiohttp.web.json_response(
                {
                    "success": False,
                    "error": f"Connection failed with error code {result}",
                }
            )
    except socket.gaierror as e:
        # DNS resolution failed - record as Warning event
        record_event(
            "Warning",
            resource,
            f"ConnectionTest: DNS resolution failed for {host}:{port} - {str(e)}",
        )
        return aiohttp.web.json_response(
            {"success": False, "error": f"DNS resolution failed: {e}"}
        )
    except socket.timeout:
        # Connection timeout - record as Warning event
        record_event(
            "Warning", resource, f"ConnectionTest: Connection timeout to {host}:{port}"
        )
        return aiohttp.web.json_response(
            {"success": False, "error": "Connection timeout"}
        )
    except Exception as e:
        # Unexpected error - record as Error event
        record_event(
            "Error",
            resource,
            f"ConnectionTest: Unexpected error testing {host}:{port} - {str(e)}",
        )
        return aiohttp.web.json_response(
            {"success": False, "error": f"Unexpected error: {e}"}
        )


async def handle_healthz(request):
    """Health check endpoint for Kubernetes liveness/readiness probes"""
    return aiohttp.web.json_response({"status": "ok"})


def _run_webserver_in_thread(
    port: int, loop: asyncio.AbstractEventLoop, shutdown_event: threading.Event
):
    """Run webserver in a separate thread with its own event loop

    Args:
        port: Port to run the webserver on
        loop: Event loop for the webserver
        shutdown_event: Threading event to signal shutdown
    """
    asyncio.set_event_loop(loop)

    async def _start_server():
        if DRYRUN_MODE:
            logger.info("Running in DRYRUN mode - operator will be read-only")

        if AUTH_ENABLED:
            logger.info("Authentication enabled for debug webserver")

        # Create aiohttp app with authentication middleware
        app = aiohttp.web.Application(middlewares=[auth_middleware])

        # Setup routes
        app.router.add_get("/", handle_index)
        app.router.add_get("/api/state", handle_state)
        app.router.add_get("/api/topology", handle_topology)
        app.router.add_get("/api/test-tcp", handle_test_tcp)
        app.router.add_get(
            "/healthz", handle_healthz
        )  # Health check endpoint (no auth required)

        # Run server
        runner = aiohttp.web.AppRunner(app)
        await runner.setup()
        site = aiohttp.web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        logger.info(f"Debug webserver started on http://0.0.0.0:{port}")

        # Keep the server running until shutdown is signaled
        try:
            # Check shutdown_event periodically instead of waiting indefinitely
            while not shutdown_event.is_set():
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            logger.debug("Webserver task cancelled")
        finally:
            logger.info("Webserver shutting down...")
            await runner.cleanup()
            logger.debug("Webserver cleanup complete")

    try:
        loop.run_until_complete(_start_server())
    except Exception as e:
        logger.error(f"Webserver thread error: {e}", exc_info=True)
    finally:
        # Clean up the event loop
        try:
            # Cancel all remaining tasks
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            # Wait for tasks to complete cancellation
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.close()
        except Exception as e:
            logger.debug(f"Error during event loop cleanup: {e}")


def start_webserver_thread(port: int = 8080):
    """Start the debug webserver in a separate daemon thread

    Args:
        port: Port to run the webserver on

    Returns:
        tuple: (threading.Thread, threading.Event) - The webserver thread and shutdown event
    """
    # Create a new event loop for the webserver thread
    loop = asyncio.new_event_loop()

    # Create shutdown event for graceful termination
    shutdown_event = threading.Event()

    # Create and start the thread
    thread = threading.Thread(
        target=_run_webserver_in_thread,
        args=(port, loop, shutdown_event),
        daemon=True,
        name="WebserverThread",
    )
    thread.start()
    logger.info("Webserver thread started (daemon)")

    return thread, shutdown_event


async def start_webserver_async(port: int = 8080, memo=None):
    """Start the debug webserver using aiohttp (async version)

    NOTE: This function is deprecated. Use start_webserver_thread() instead
    for better isolation.
    """
    if DRYRUN_MODE:
        logger.info("Running in DRYRUN mode - operator will be read-only")

    # Create aiohttp app
    app = aiohttp.web.Application()

    # Setup routes
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/state", handle_state)
    app.router.add_get("/api/topology", handle_topology)
    app.router.add_get("/api/test-tcp", handle_test_tcp)

    # Run server in background
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Debug webserver started on http://0.0.0.0:{port}")

    return runner


# Export functions for use in main.py
__all__ = [
    "start_webserver_thread",
    "start_webserver_async",
    "record_event",
    "update_mux_state",
    "delete_mux_state",
    "DRYRUN_MODE",
]
