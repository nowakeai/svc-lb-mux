import json
import logging
import os
import queue
import sys
import time

import kopf
from kr8s.objects import Endpoints, Service

sys.path.append(os.path.dirname(__file__))

import events
import webserver
from annotations import format_summary_annotation, format_topology_annotation
from config import (
    ANNOTATION_CHANNELS,
    ANNOTATION_SUMMARY,
    ANNOTATION_TOPOLOGY,
    API_PREFIX,
    DAEMON_QUEUE_TIMEOUT,
    DEBUG_WEB_ENABLED,
    DEBUG_WEB_PORT,
    DRYRUN_MODE,
    FINALIZER,
    get_annotation,
)
from models import MuxPort
from port_allocations import (
    ConfigMapAllocationStore,
    PortAllocator,
    allocation_configmap_name,
    requested_port_range,
)
from reconcile import (
    build_generated_endpoints_metadata,
    build_mux_ports,
    collect_auto_allocation_keys,
    collect_channel_endpoints,
    collect_static_port_claims,
    count_ready_channel_pods,
    get_current_endpoints_set,
    get_old_endpoints_set,
    effective_mux_max_ports,
    find_mux_port_conflicts,
    gke_max_ports_warning,
    process_channel_ports,
    update_channel_service_metadata,
    would_exceed_mux_port_limit,
)
from refs import get_mux_from_lb_class

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


def build_port_allocator(mux, mux_key, channels, event_body):
    try:
        ranges = requested_port_range(mux)
    except ValueError as error:
        events.error(
            event_body,
            reason="InvalidPortRange",
            message=str(error),
        )
        return None, None

    if not ranges:
        return None, None

    store = ConfigMapAllocationStore(
        namespace=mux.namespace,
        name=allocation_configmap_name(mux),
        mux_key=mux_key,
    )
    try:
        state = store.load()
        reserved_ports = collect_static_port_claims(channels)
        active_keys = collect_auto_allocation_keys(channels)
    except ValueError as error:
        events.error(
            event_body,
            reason="PortAllocationStoreInvalid",
            message=str(error),
        )
        return None, None

    return (
        PortAllocator(
            mux_key,
            ranges,
            state,
            reserved_ports=reserved_ports,
            active_keys=active_keys,
        ),
        store,
    )


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
        port_owners = {}

        try:
            max_ports_warning = gke_max_ports_warning(mux)
            max_ports = effective_mux_max_ports(mux)
        except ValueError as error:
            events.error(
                body,
                reason="InvalidMaxPorts",
                message=str(error),
            )
            max_ports_warning = None
            max_ports = 0

        if max_ports_warning:
            events.warn(
                body,
                reason="GkePortLimitApplied",
                message=max_ports_warning,
            )

        sorted_channels = sorted(
            tuple(channels),
            key=lambda item: (item["metadata"]["namespace"], item["metadata"]["name"]),
        )
        allocator, allocation_store = build_port_allocator(
            mux, mux_key, sorted_channels, body
        )
        for ch in sorted_channels:
            if would_exceed_mux_port_limit(ports, ch, max_ports):
                events.error(
                    ch,
                    reason="MuxPortLimitExceeded",
                    message=(
                        f"mux {namespace}/{name} is limited to {max_ports} port(s); "
                        f"skipping channel would exceed the GKE Service LoadBalancer port limit"
                    ),
                )
                continue

            try:
                ch_ports, ch_ports_anno, channel_service = process_channel_ports(
                    ch, memo, allocator=allocator
                )
            except ValueError as error:
                events.error(
                    ch,
                    reason="InvalidPortMapping",
                    message=str(error),
                )
                continue

            conflicts = find_mux_port_conflicts(port_owners, ch, ch_ports)
            if conflicts:
                events.error(
                    ch,
                    reason="MuxPortConflict",
                    message="; ".join(conflicts),
                )
                continue

            ports.update(ch_ports)
            update_channel_service_metadata(
                ch, channel_service, ch_ports_anno, status_lb
            )

            ch_endpoints = collect_channel_endpoints(ch, channel_service, memo)
            endpoints.extend(ch_endpoints)

        if allocator and allocation_store:
            try:
                allocation_state = allocator.to_state()
                if allocator.changed:
                    allocation_store.save(allocation_state)
            except ValueError as error:
                events.error(
                    body,
                    reason="PortAllocationStoreInvalid",
                    message=str(error),
                )

        endpoint_labels, endpoint_annotations = build_generated_endpoints_metadata(
            mux.labels, mux_key, sorted_channels
        )
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
        old_metadata = ep._raw.get("metadata", {})
        old_labels = dict(old_metadata.get("labels", {}))
        old_annotations = dict(old_metadata.get("annotations", {}))

        # Collect old endpoints for comparison before replacing desired metadata/subsets.
        old_endpoints, old_not_ready_endpoints = get_old_endpoints_set(ep._raw)

        ep.labels = endpoint_labels
        ep.annotations = endpoint_annotations
        kopf.adopt(ep._raw)

        ep["subsets"] = endpoints
        mux_ports = build_mux_ports(ports)
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
        total_pods = count_ready_channel_pods(channels, memo)
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

            metadata_changed = (
                old_labels != endpoint_labels
                or old_annotations != endpoint_annotations
            )

            # Compare metadata and both ready and not-ready endpoints to detect all changes.
            if (
                metadata_changed
                or old_endpoints != current_endpoints
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
                        {
                            "metadata": {
                                "labels": endpoint_labels,
                                "annotations": endpoint_annotations,
                            },
                            "subsets": ep["subsets"],
                        }
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
