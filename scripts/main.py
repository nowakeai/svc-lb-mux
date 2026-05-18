import json
import logging
import os
import queue
import re
import sys
import time
from collections import namedtuple
from hashlib import sha256

import kopf
from kr8s.objects import Endpoints, Service

sys.path.append(os.path.dirname(__file__))

import events
import utils
import webserver
from webserver import DRYRUN_MODE

current_format = (
    logging.root.handlers[0].formatter._fmt if logging.root.handlers else None
)
current_level = logging.root.handlers[0].level if logging.root.handlers else None
logging.basicConfig(
    level=current_level or logging.INFO,
    format=current_format
    or "[%(asctime)s] [%(levelname)s] [%(module)s:%(funcName)s:%(lineno)d] %(message)s",
)
# print logger level
print(f"Current Log Level: {logging.getLevelName(logging.getLogger().level)}")
if os.getenv("SVC_LB_MUX_DEBUG", "").lower() in ("true", "on", "yes"):
    logging.getLogger().setLevel(logging.DEBUG)
# mute kr8s logger
logging.getLogger("kr8s").setLevel(logging.WARN)

NAMESPACE = os.environ.get("NAMESPACE", "default")
DEFAULT_MUX_NAMESPACE = os.environ.get("DEFAULT_MUX_NAMESPACE", NAMESPACE)
POD_NAME = os.environ.get("POD_NAME", "svc-lb-mux")
DEBUG_WEB_ENABLED = os.environ.get("DEBUG_WEB_ENABLED", "true").lower() in ("true", "1", "yes", "on")
DEBUG_WEB_PORT = int(os.environ.get("DEBUG_WEB_PORT", "8080"))

# Constants for daemon queue timeout
DAEMON_QUEUE_TIMEOUT = 10  # seconds to wait for queue events

API_PREFIX = os.environ.get("API_PREFIX", "svc-mux.nowake.ai").strip() or "svc-mux.nowake.ai"
DNS_LABEL_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
# the service is managed by the multiplexer
ANNOTATION_MULTIPLEXER = f"{API_PREFIX}/multiplexer"
ANNOTATION_DISABLED = f"{API_PREFIX}/disabled"
ANNOTATION_PORTS = f"{API_PREFIX}/ports"
ANNOTATION_CHANNELS = f"{API_PREFIX}/channels"
ANNOTATION_TOPOLOGY = f"{API_PREFIX}/topology"  # Human-readable topology summary
ANNOTATION_SUMMARY = f"{API_PREFIX}/summary"  # One-line summary
FINALIZER = f"{API_PREFIX}/finalizer"



@kopf.on.startup()
async def operator_startup(memo: kopf.Memo, settings: kopf.OperatorSettings, **_):
    # settings
    settings.posting.level = logging.WARN
    settings.persistence.finalizer = FINALIZER
    settings.execution.max_workers = 64

    # global memos
    # Map[Mux(ns, name)] => Queue
    memo.mux_queues = {}
    # Map[(ns, name)] => Endpoints
    memo.endpoints = {}

    # Start debug webserver in a separate thread (if enabled)
    # This isolates the webserver from the kopf event loop
    if DEBUG_WEB_ENABLED:
        webserver_thread, shutdown_event = webserver.start_webserver_thread(DEBUG_WEB_PORT)
        memo.webserver_thread = webserver_thread
        memo.webserver_shutdown = shutdown_event
        logging.info(
            f"Debug webserver started on port {DEBUG_WEB_PORT} (in separate thread)"
        )
    else:
        memo.webserver_thread = None
        memo.webserver_shutdown = None
        logging.info("Debug webserver is disabled")


@kopf.on.cleanup()
async def operator_cleanup(memo: kopf.Memo, **_):
    """Clean up resources on operator shutdown"""
    logging.info("Shutting down operator, cleaning up resources...")

    # Signal webserver to shutdown
    shutdown_event = getattr(memo, "webserver_shutdown", None)
    webserver_thread = getattr(memo, "webserver_thread", None)
    if shutdown_event:
        logging.info("Signaling webserver to shutdown...")
        shutdown_event.set()

        # Wait for webserver thread to finish (with timeout)
        if webserver_thread and webserver_thread.is_alive():
            webserver_thread.join(timeout=3.0)
            if webserver_thread.is_alive():
                logging.warning("Webserver thread did not shutdown cleanly")
            else:
                logging.info("Webserver shutdown complete")

    logging.info("Operator cleanup complete")


def annotation_key(name: str, prefix: str = API_PREFIX) -> str:
    return f"{prefix}/{name}"


def get_annotation(annotations: dict, name: str, default=None):
    return annotations.get(annotation_key(name), default)


def has_multiplexer_annotation(body, **_):
    annotations = body["metadata"].get("annotations", {})
    return get_annotation(annotations, "multiplexer") == "true"


def is_multiplexer_service(body, **_):
    return (
        has_multiplexer_annotation(body)
        and body["spec"].get("type") == "LoadBalancer"
        and not body["spec"].get("selector")
    )


def is_multiplexer_endpoints(name, namespace, multiplexer_services: kopf.Index, **_):
    return (namespace, name) in {
        (ns, svc["metadata"]["name"])
        for ns, svcs in multiplexer_services.items()
        for svc in svcs
    }


def is_channel_service(spec, **_):
    """
    Check if the service is a channel service
    """
    lb_class = spec.get("loadBalancerClass", "")
    return spec.get("type") == "LoadBalancer" and lb_class.startswith(
        API_PREFIX + "/"
    )


def is_channel_endpoints(name, namespace, channel_services: kopf.Index, **_):
    return (namespace, name) in {
        (ns, svc["metadata"]["name"])
        for ns, svcs in channel_services.items()
        for svc in svcs
    }


@kopf.index("services", when=has_multiplexer_annotation)
def multiplexer_services(name, namespace, body, memo: kopf.Memo, **_):
    if body["spec"].get("type") != "LoadBalancer":
        events.error(
            body,
            reason="NotLoadBalancer",
            message="Multiplexer service has to be of type LoadBalancer",
        )
        return
    if body["spec"].get("selector"):
        events.error(
            body,
            reason="NotSupported",
            message="Multiplexer service with selectors are not supported",
        )
        return
    memo.mux_queues.setdefault((namespace, name), queue.Queue())
    return {namespace: body}


@kopf.index("services", when=is_channel_service)
def channel_services(namespace, body, **_):
    return {namespace: body}


@kopf.index("services", when=is_channel_service)
def mux_channels(body, **_):
    # Parse the loadBalancerClass to get the mux namespace and name
    # Handle invalid format gracefully to prevent index failure
    try:
        mux = get_mux_from_lb_class(body["spec"]["loadBalancerClass"])
        logging.info(
            "Multiplexer service %s/%s for channel service %s/%s",
            mux[0],
            mux[1],
            body["metadata"]["namespace"],
            body["metadata"]["name"],
        )
        return {mux: body}
    except ValueError as e:
        # Log error and record event, but don't fail the indexing
        events.error(
            body,
            reason="InvalidLoadBalancerClass",
            message=f"Invalid loadBalancerClass format: {e}",
        )
        logging.error(
            "Failed to parse loadBalancerClass for %s/%s: %s",
            body["metadata"]["namespace"],
            body["metadata"]["name"],
            e,
        )
        # Return empty dict to skip indexing this resource
        return {}


def _validate_dns_label(value: str, field: str):
    if not value or len(value) > 63 or not DNS_LABEL_RE.match(value):
        raise ValueError(
            f"Invalid {field} {value!r}; expected a Kubernetes DNS label"
        )


def get_mux_from_lb_class(cls: str):
    """Parse loadBalancerClass to extract multiplexer namespace and name.

    Format: <api-prefix>/<name>[.<namespace>]

    Args:
        cls: The loadBalancerClass string

    Returns:
        tuple: (namespace, name) of the multiplexer service

    Raises:
        ValueError: If the format is invalid
    """
    prefix = API_PREFIX + "/"
    if not cls.startswith(prefix):
        expected = f"{API_PREFIX}/<name>[.<namespace>]"
        raise ValueError(f"Invalid LoadBalancerClass, expected format: {expected}")
    mux_ref = cls[len(prefix) :]

    split_parts = mux_ref.split(".")
    if len(split_parts) == 1:
        mux_name = split_parts[0]
        mux_ns = DEFAULT_MUX_NAMESPACE
    elif len(split_parts) == 2:
        mux_name, mux_ns = split_parts
    else:
        raise ValueError(
            f"Invalid LoadBalancerClass {cls!r}; expected {API_PREFIX}/<name>[.<namespace>]"
        )

    _validate_dns_label(mux_name, "multiplexer service name")
    _validate_dns_label(mux_ns, "multiplexer namespace")
    return mux_ns, mux_name


def trigger_mux(channel: kopf.Body, memo: kopf.Memo, no_raise=False):
    mux_ns, mux_name = get_mux_from_lb_class(channel["spec"]["loadBalancerClass"])
    mux_queues = memo.mux_queues

    # Try to get the queue with retries and longer waits
    # Mux daemon might be starting up or the mux service might not exist yet
    Q = None
    for attempt in range(5):  # Increased from 3 to 5 attempts
        Q = mux_queues.get((mux_ns, mux_name))
        if Q:
            break
        if attempt < 4:  # Don't sleep on last attempt
            time.sleep(2)  # Increased from 1 to 2 seconds

    if not Q:
        # Only emit error event if mux service should exist but queue is not ready
        # This reduces false positives during startup
        if not no_raise:
            logging.debug(
                f"Multiplexer service {mux_ns}/{mux_name} queue not ready for channel "
                f"{channel['metadata']['namespace']}/{channel['metadata']['name']}, "
                f"will retry later"
            )
            raise kopf.TemporaryError("Multiplexer not ready", delay=10)
        else:
            # During deletion, just log and return silently
            logging.debug(
                f"Multiplexer service {mux_ns}/{mux_name} not ready, "
                f"skipping channel update (deletion case)"
            )
            return

    Q.put(channel)


@kopf.on.create("endpoints", when=is_channel_endpoints)
@kopf.on.update("endpoints", when=is_channel_endpoints)
@kopf.on.resume("endpoints", when=is_channel_endpoints)
def handle_endpoints(
    name,
    namespace,
    body,
    memo: kopf.Memo,
    channel_services: kopf.Index,
    **_,
):
    for svc in channel_services.get(namespace, []):
        if not svc["metadata"]["name"] == name:
            continue
        memo.endpoints[(namespace, name)] = body
        try:
            trigger_mux(svc, memo)
        except ValueError as e:
            events.error(
                svc,
                reason="InvalidLoadBalancerClass",
                message=f"Invalid loadBalancerClass format: {e}",
            )


@kopf.on.delete("services", when=is_channel_service)
def channel_deletion(
    name,
    namespace,
    body,
    memo: kopf.Memo,
    **_,
):
    # Clean up endpoints cache for the deleted channel
    memo.endpoints.pop((namespace, name), None)

    # Trigger mux update to remove this channel's ports from the multiplexer
    # Note: Don't manually modify the Index, kopf manages it automatically
    # The index will be updated when kopf re-evaluates the index function
    try:
        trigger_mux(body, memo, no_raise=True)
    except ValueError as e:
        logging.debug(
            "Skipping channel deletion for invalid loadBalancerClass on %s/%s: %s",
            namespace,
            name,
            e,
        )


@kopf.on.create("services", when=is_channel_service)
@kopf.on.update("services", when=is_channel_service)
@kopf.on.resume("services", when=is_channel_service)
def handle_channel_service(
    body,
    memo: kopf.Memo,
    **_,
):
    # if any ports does not have name, create a error event
    for n, p in enumerate(body["spec"]["ports"]):
        if not p.get("name"):
            events.error(
                body,
                reason="InvalidPort",
                message="Port name is required",
                fieldPath=f"spec.ports[{n}]",
            )
            return
    try:
        trigger_mux(body, memo)
    except ValueError as e:
        events.error(
            body,
            reason="InvalidLoadBalancerClass",
            message=f"Invalid loadBalancerClass format: {e}",
        )
        return


@kopf.on.create("endpoints", when=is_multiplexer_endpoints)
@kopf.on.update("endpoints", when=is_multiplexer_endpoints)
@kopf.on.resume("endpoints", when=is_multiplexer_endpoints)
def mux_endpoints(name, namespace, body, memo: kopf.Memo, **_):
    memo.endpoints[(namespace, name)] = body


@kopf.on.delete("services", when=is_multiplexer_service)
def mux_deletion(name, namespace, memo: kopf.Memo, **_):
    mux_key = (namespace, name)
    memo.endpoints.pop(mux_key, None)
    memo.mux_queues.pop(mux_key, None)

    # Clean up webserver state
    webserver.delete_mux_state(mux_key)
    webserver.record_event("Normal", f"{namespace}/{name}", "Mux deleted")


def port_hash(*parts) -> str:
    return sha256("/".join(parts).encode()).hexdigest()[:7]


class MuxPort(namedtuple("MuxPort", ["name", "port", "protocol"])):
    pass


class MuxEp(namedtuple("MuxEp", ["ip", "port", "protocol"])):
    pass


def format_topology_annotation(channels, memo, mux_external_dns=None):
    """Generate human-readable topology annotation for mux service.

    Args:
        channels: List of channel service bodies
        memo: kopf memo containing endpoints
        mux_external_dns: External DNS/IP of the mux

    Returns:
        str: Human-readable topology description
    """
    lines = []
    total_ports = 0
    total_ready_pods = 0

    # Format mux DNS (may have multiple hostnames)
    if mux_external_dns:
        mux_dns_display = utils.format_dns_display(mux_external_dns)
        lines.append(f"Mux DNS: {mux_dns_display or mux_external_dns}")
        lines.append("")

    lines.append("Channels:")
    for ch in sorted(channels, key=lambda c: (c["metadata"]["namespace"], c["metadata"]["name"])):
        ch_ns = ch["metadata"]["namespace"]
        ch_name = ch["metadata"]["name"]
        ch_key = (ch_ns, ch_name)

        # Get channel DNS (may have multiple hostnames)
        ch_annotations = ch.get("metadata", {}).get("annotations", {})
        ch_dns_annotation = ch_annotations.get("external-dns.alpha.kubernetes.io/hostname")
        custom_dns_marker = " (custom)" if ch_dns_annotation else ""

        # Format DNS display
        if ch_dns_annotation:
            ch_dns_display = utils.format_dns_display(ch_dns_annotation) or ch_dns_annotation
        else:
            # Fallback to mux DNS
            ch_dns_display = utils.get_primary_dns(mux_external_dns) or "pending"

        lines.append(f"  - {ch_ns}/{ch_name}")
        lines.append(f"    DNS: {ch_dns_display}{custom_dns_marker}")

        # Get port mappings
        chep = memo.endpoints.get(ch_key)
        ready_pods_count = 0
        if chep and "subsets" in chep:
            for subset in chep["subsets"]:
                ready_pods_count += len(subset.get("addresses", []))

        ports_line = "    Ports:"
        for p in ch["spec"].get("ports", []):
            port_name = p.get("name", "unnamed")
            channel_port = p.get("port")
            node_port = p.get("nodePort", "pending")
            target_port = p.get("targetPort", channel_port)
            protocol = p.get("protocol", "TCP")

            ports_line += f" {port_name}:{channel_port}->{node_port}"
            total_ports += 1

        lines.append(ports_line)
        lines.append(f"    Backend: {ready_pods_count} pod(s) ready")
        total_ready_pods += ready_pods_count

    lines.append("")
    lines.append(f"Summary: {len(channels)} channel(s), {total_ports} port(s), {total_ready_pods} backend pod(s)")

    return "\n".join(lines)


def format_summary_annotation(channels, total_ports, total_pods, mux_external_dns=None):
    """Generate one-line summary annotation.

    Args:
        channels: List of channel service bodies
        total_ports: Total number of ports
        total_pods: Total number of backend pods
        mux_external_dns: External DNS/IP of the mux

    Returns:
        str: One-line summary
    """
    # Format DNS display (show primary + count if multiple)
    dns_display = utils.format_dns_display(mux_external_dns)
    dns_part = f"DNS: {dns_display}" if dns_display else "DNS: pending"

    return f"{len(channels)} channel(s) | {total_ports} port(s) | {total_pods} pod(s) | {dns_part}"


def format_channel_port_annotation(ports_list):
    """Generate human-readable port annotation for channel service.

    Args:
        ports_list: List of (port_name, channel_port, mux_port) tuples

    Returns:
        str: Human-readable port mapping like "rpc:8545->30001, ws:8546->30002"
    """
    return ", ".join([f"{name}:{ch_port}->{mux_port}" for name, ch_port, mux_port in ports_list])


def process_channel_ports(channel, memo):
    """Process ports from a channel service and return mux ports and port annotations.

    Args:
        channel: Channel service body
        memo: kopf memo containing endpoints

    Returns:
        tuple: (set of MuxPort, human-readable port annotation string, kr8s Service object)
    """
    c = Service(channel)
    ports = set()
    ports_for_anno = []  # List of (port_name, channel_port, mux_port) tuples

    for p in channel["spec"]["ports"]:
        node_port = p.get("nodePort")
        if node_port is None:
            # Only log at DEBUG level to reduce noise
            logging.debug(
                "NodePort not yet allocated for channel %s/%s port %s, skipping",
                c.namespace,
                c.name,
                p.get("name", "unnamed"),
            )
            continue

        port_name_hash = port_hash(c.namespace, c.name, p["name"])
        ports.add(MuxPort(port_name_hash, node_port, p["protocol"]))

        # For human-readable annotation
        port_display_name = p.get("name", "unnamed")
        ports_for_anno.append((port_display_name, p['port'], node_port))

    # Generate human-readable annotation
    ports_anno_str = format_channel_port_annotation(ports_for_anno)

    return ports, ports_anno_str, c


def collect_channel_endpoints(channel, channel_service, memo):
    """Collect endpoints from a channel service.

    Args:
        channel: Channel service body
        channel_service: kr8s Service object
        memo: kopf memo containing endpoints

    Returns:
        list: List of endpoint subsets
    """
    endpoints = []
    chep = memo.endpoints.get((channel_service.namespace, channel_service.name))

    if chep and "subsets" in chep:
        for subset in chep["subsets"]:
            addresses = subset.get("addresses", [])
            not_ready_addresses = subset.get("notReadyAddresses", [])

            # Skip subset if both addresses and notReadyAddresses are empty
            if not addresses and not not_ready_addresses:
                # Only log at DEBUG level to reduce noise
                logging.debug(
                    "Skipping empty subset for channel %s/%s - no addresses or notReadyAddresses",
                    channel_service.namespace,
                    channel_service.name,
                )
                continue

            ss = {"addresses": addresses}
            if not_ready_addresses:
                ss["notReadyAddresses"] = not_ready_addresses

            for p in subset.get("ports", []):
                port_name = port_hash(
                    channel_service.namespace, channel_service.name, p["name"]
                )
                ss.setdefault("ports", []).append(
                    {
                        "name": port_name,
                        "port": p["port"],
                        "protocol": p["protocol"],
                    }
                )
            endpoints.append(ss)

    return endpoints



def update_channel_service_metadata(channel, channel_service, ports_anno, status_lb):
    """Update channel service annotations and status.

    Args:
        channel: Channel service body
        channel_service: kr8s Service object
        ports_anno: Port annotation string
        status_lb: LoadBalancer status to sync
    """
    # Update annotations
    if (
        channel.get("metadata", {}).get("annotations", {}).get(ANNOTATION_PORTS)
        != ports_anno
    ):
        if not DRYRUN_MODE:
            channel_service.patch(
                {"metadata": {"annotations": {ANNOTATION_PORTS: ports_anno}}}
            )
        else:
            # Only log at DEBUG level in dryrun to reduce noise
            logging.debug(
                f"[DRYRUN] Would patch channel {channel_service.namespace}/{channel_service.name} annotations"
            )

    # Update status
    if channel.get("status", {}).get("loadBalancer", {}) != status_lb:
        if not DRYRUN_MODE:
            channel_service.patch(
                {"status": {"loadBalancer": status_lb}}, subresource="status"
            )
        else:
            # Only log at DEBUG level in dryrun to reduce noise
            logging.debug(
                f"[DRYRUN] Would patch channel {channel_service.namespace}/{channel_service.name} status"
            )


def get_old_endpoints_set(ep_raw):
    """Extract old endpoints from endpoint object for comparison.

    Args:
        ep_raw: Raw endpoint object

    Returns:
        tuple: (set of ready endpoints, set of not-ready endpoints)
    """
    old_endpoints = set()
    old_not_ready_endpoints = set()

    if "subsets" in ep_raw:
        for subset in ep_raw["subsets"]:
            ready_ips = [a["ip"] for a in subset.get("addresses", [])]
            not_ready_ips = [a["ip"] for a in subset.get("notReadyAddresses", [])]
            for port in subset.get("ports", []):
                for ip in ready_ips:
                    old_endpoints.add(MuxEp(ip, port["port"], port["protocol"]))
                for ip in not_ready_ips:
                    old_not_ready_endpoints.add(
                        MuxEp(ip, port["port"], port["protocol"])
                    )

    return old_endpoints, old_not_ready_endpoints


def get_current_endpoints_set(ep):
    """Extract current endpoints from endpoint object for comparison.

    Args:
        ep: Endpoint object

    Returns:
        tuple: (set of ready endpoints, set of not-ready endpoints)
    """
    current_endpoints = set(
        (
            MuxEp(a["ip"], p["port"], p["protocol"])
            for s in ep["subsets"]
            for a in s.get("addresses", [])
            for p in s.get("ports", [])
        )
    )
    current_not_ready_endpoints = set(
        (
            MuxEp(a["ip"], p["port"], p["protocol"])
            for s in ep["subsets"]
            for a in s.get("notReadyAddresses", [])
            for p in s.get("ports", [])
        )
    )
    return current_endpoints, current_not_ready_endpoints


@kopf.daemon("services", when=is_multiplexer_service)
def mux_daemon(
    namespace,
    name,
    body,
    memo: kopf.Memo,
    mux_channels: kopf.Index,
    stopped,
    **_,
):
    Q: queue.Queue = memo.mux_queues.setdefault((namespace, name), queue.Queue())
    mux_key = (namespace, name)

    # Create Service object and refresh to get latest state from API server
    # This is crucial for getting up-to-date LoadBalancer status and annotations
    mux = Service(body)
    mux.refresh()

    # Update webserver state with current mux service data
    webserver.update_mux_state(memo, mux_channels, mux_key, mux._raw)

    while not stopped:
        try:
            # Wait for channel service updates with configurable timeout
            event = Q.get(timeout=DAEMON_QUEUE_TIMEOUT)
            logging.info(
                "got update from channel service %s/%s",
                event["metadata"]["namespace"],
                event["metadata"]["name"],
            )
        except queue.Empty:
            pass

        # Refresh mux service to get latest state from API server
        # This is crucial for getting up-to-date LoadBalancer status and annotations
        mux.refresh()
        status_lb = mux.status.get("loadBalancer")

        # Update webserver state on each iteration with refreshed data
        webserver.update_mux_state(memo, mux_channels, mux_key, mux._raw)

        old_ports = set(
            (MuxPort(p["name"], p["port"], p["protocol"]) for p in mux.spec.ports)
        )

        channels = mux_channels.get((namespace, name), set())
        endpoints = []
        ports = set()

        # Use tuple() to avoid "set changed size during iteration" if index updates
        for ch in tuple(channels):
            # Process channel ports and get port annotations
            ch_ports, ch_ports_anno, channel_service = process_channel_ports(ch, memo)
            ports.update(ch_ports)

            # Update channel service metadata (annotations and status)
            # ch_ports_anno is already a formatted string like "rpc:8545->30001, ws:8546->30002"
            update_channel_service_metadata(
                ch, channel_service, ch_ports_anno, status_lb
            )

            # Collect endpoints from channel service
            ch_endpoints = collect_channel_endpoints(ch, channel_service, memo)
            endpoints.extend(ch_endpoints)

        ep = Endpoints(
            memo.endpoints.get(
                (namespace, name),
                {
                    "metadata": {
                        "name": name,
                        "namespace": namespace,
                    },
                    "subsets": [],
                },
            )
        )
        ep.labels = mux.labels
        kopf.adopt(ep._raw)

        # Collect old endpoints for comparison
        old_endpoints, old_not_ready_endpoints = get_old_endpoints_set(ep._raw)

        ep["subsets"] = endpoints
        mux_ports = [
            {
                "name": p.name,
                "port": p.port,
                "protocol": p.protocol,
            }
            for p in ports
        ]
        if mux_ports:
            mux_ports = [p for p in mux_ports if p["name"] != "placeholder"]
        else:
            mux_ports = [{"name": "placeholder", "port": 101, "protocol": "TCP"}]
        channel_list = [
            {"namespace": c["metadata"]["namespace"], "name": c["metadata"]["name"]}
            for c in channels
        ]
        anno_chans = json.dumps([f"{c['namespace']}/{c['name']}" for c in channel_list])

        # Get mux external DNS/IP for human-readable annotations
        mux_external_dns = None
        if status_lb and "ingress" in status_lb:
            ingress_list = status_lb["ingress"]
            if ingress_list:
                mux_external_dns = ingress_list[0].get("hostname") or ingress_list[0].get("ip")

        # Generate human-readable topology and summary annotations
        anno_topology = format_topology_annotation(list(channels), memo, mux_external_dns)

        # Calculate totals for summary
        total_ports = len(ports)
        total_pods = sum(
            len(subset.get("addresses", []))
            for ch_key in [(ch["metadata"]["namespace"], ch["metadata"]["name"]) for ch in channels]
            for chep in [memo.endpoints.get(ch_key)]
            if chep and "subsets" in chep
            for subset in chep["subsets"]
        )
        anno_summary = format_summary_annotation(list(channels), total_ports, total_pods, mux_external_dns)

        # Check if annotations need update
        annotations_changed = (
            mux.annotations.get(ANNOTATION_CHANNELS) != anno_chans
            or mux.annotations.get(ANNOTATION_TOPOLOGY) != anno_topology
            or mux.annotations.get(ANNOTATION_SUMMARY) != anno_summary
        )

        if annotations_changed:
            mux.annotations[ANNOTATION_CHANNELS] = anno_chans
            mux.annotations[ANNOTATION_TOPOLOGY] = anno_topology
            mux.annotations[ANNOTATION_SUMMARY] = anno_summary

            if not DRYRUN_MODE:
                mux.patch({"metadata": {"annotations": mux.annotations}})
                # Only emit event in production mode
                events.info(
                    body,
                    reason="MuxAnnotationsUpdated",
                    message=f"Mux annotations updated: {anno_summary}",
                )
            else:
                # Only log at DEBUG level in dryrun to reduce noise
                logging.debug(
                    f"[DRYRUN] Would patch mux {namespace}/{name} annotations"
                )

        current_ports = set(
            (MuxPort(p["name"], p["port"], p["protocol"]) for p in mux_ports)
        )
        if old_ports != current_ports:
            added_ports = current_ports - old_ports
            removed_ports = old_ports - current_ports
            msg_parts = []
            if added_ports:
                msg_parts.append(
                    f"Added {len(added_ports)} port(s): "
                    + ", ".join(f"{p.port}/{p.protocol}" for p in sorted(added_ports, key=lambda x: x.port))
                )
            if removed_ports:
                msg_parts.append(
                    f"Removed {len(removed_ports)} port(s): "
                    + ", ".join(f"{p.port}/{p.protocol}" for p in sorted(removed_ports, key=lambda x: x.port))
                )
            if not DRYRUN_MODE:
                # Only emit event in production mode
                events.info(
                    body,
                    reason="MuxPortsChanged",
                    message="; ".join(msg_parts) if msg_parts else f"Mux ports updated: {len(mux_ports)} port(s) total",
                )
                mux.patch({"spec": {"ports": mux_ports}})
            else:
                # Only log at DEBUG level in dryrun to reduce noise
                logging.debug(
                    f"[DRYRUN] Would patch mux {namespace}/{name} ports: {len(mux_ports)} ports"
                )

        if ep.exists():
            # Collect current endpoints including both ready and not-ready addresses
            current_endpoints, current_not_ready_endpoints = get_current_endpoints_set(
                ep
            )

            # Compare both ready and not-ready endpoints to detect all changes
            if (
                old_endpoints != current_endpoints
                or old_not_ready_endpoints != current_not_ready_endpoints
            ):
                added_eps = current_endpoints - old_endpoints
                removed_eps = old_endpoints - current_endpoints
                msg_parts = []
                if added_eps:
                    msg_parts.append(
                        f"Added {len(added_eps)} endpoint(s): "
                        + ", ".join(f"{e.ip}:{e.port}/{e.protocol}" for e in sorted(added_eps, key=lambda x: (x.ip, x.port)))
                    )
                if removed_eps:
                    msg_parts.append(
                        f"Removed {len(removed_eps)} endpoint(s): "
                        + ", ".join(f"{e.ip}:{e.port}/{e.protocol}" for e in sorted(removed_eps, key=lambda x: (x.ip, x.port)))
                    )

                # Count total ready endpoints
                total_ready = len(current_endpoints)
                total_not_ready = len(current_not_ready_endpoints)
                summary = f"{total_ready} ready, {total_not_ready} not ready"

                if not DRYRUN_MODE:
                    # Only emit event in production mode
                    events.info(
                        body,
                        reason="MuxEndpointsChanged",
                        message="; ".join(msg_parts) if msg_parts else f"Mux endpoints updated: {summary}",
                    )
                    ep.patch(
                        {"metadata": {"labels": mux.labels}, "subsets": ep["subsets"]}
                    )
                else:
                    # Only log at DEBUG level in dryrun to reduce noise
                    logging.debug(f"[DRYRUN] Would patch endpoints {namespace}/{name}")
        else:
            if not DRYRUN_MODE:
                # Count total endpoints being created
                total_ready = sum(
                    len(subset.get("addresses", []))
                    for subset in endpoints
                )
                total_not_ready = sum(
                    len(subset.get("notReadyAddresses", []))
                    for subset in endpoints
                )
                # Only emit event in production mode
                events.info(
                    body,
                    reason="MuxEndpointsCreated",
                    message=f"Mux endpoints created: {total_ready} ready, {total_not_ready} not ready",
                )
                ep.create()
            else:
                # Only log at DEBUG level in dryrun to reduce noise
                logging.debug(f"[DRYRUN] Would create endpoints {namespace}/{name}")
    memo.mux_queues.pop((namespace, name), None)


if __name__ == "__main__":
    import signal

    logging.basicConfig(
        level=logging.DEBUG,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        stream=sys.stdout,
    )

    # Set up graceful shutdown on Ctrl+C
    # Use a mutable container to avoid nonlocal issues with linters
    shutdown_state = {"requested": False}
    original_sigint_handler = signal.getsignal(signal.SIGINT)

    def graceful_exit_handler(signum, frame):  # noqa: ARG001
        if not shutdown_state["requested"]:
            logging.info("\nReceived interrupt signal, initiating graceful shutdown...")
            shutdown_state["requested"] = True
            # Let kopf handle the shutdown gracefully
            # Restore original handler for second Ctrl+C to force exit
            signal.signal(signal.SIGINT, original_sigint_handler)
        else:
            # Second Ctrl+C - force exit
            logging.warning("Force shutdown requested")
            sys.exit(1)

    signal.signal(signal.SIGINT, graceful_exit_handler)
    logging.info("Press Ctrl+C to shutdown gracefully (twice to force)")

    kopf.run(liveness_endpoint="http://0.0.0.0:8888/healthz", clusterwide=True)
