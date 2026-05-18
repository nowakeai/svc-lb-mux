"""Pure and Kubernetes-facing helpers for mux reconciliation."""

import logging
from hashlib import sha256

from kr8s.objects import Service

from annotations import format_channel_port_annotation
from config import (
    ANNOTATION_CHANNELS,
    ANNOTATION_MANAGED,
    ANNOTATION_EXTERNAL_PORTS,
    ANNOTATION_PORTS,
    DRYRUN_MODE,
    annotation_key,
)
from models import MuxEp, MuxPort
from port_allocations import AUTO_PORT, PortAllocationRef


def port_hash(*parts) -> str:
    return sha256("/".join(parts).encode()).hexdigest()[:7]


def parse_external_ports_annotation(value: str):
    """Parse explicit mux external ports from "name:port,name2:port"."""
    ports = {}
    if not value:
        return ports

    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" not in item:
            raise ValueError(
                f"Invalid {ANNOTATION_EXTERNAL_PORTS} entry {item!r}; expected name:port"
            )
        name, port_text = item.split(":", 1)
        name = name.strip()
        if not name:
            raise ValueError(
                f"Invalid {ANNOTATION_EXTERNAL_PORTS} entry {item!r}; port name is required"
            )
        if port_text.strip().lower() == AUTO_PORT:
            ports[name] = AUTO_PORT
        else:
            ports[name] = validate_port_number(port_text.strip(), ANNOTATION_EXTERNAL_PORTS)

    return ports


def validate_port_number(value, field: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid {field} port {value!r}; expected integer") from error

    if not 1 <= port <= 65535:
        raise ValueError(f"Invalid {field} port {port}; expected 1-65535")
    return port


def resolve_channel_external_port(port, explicit_ports, allocator=None, channel=None):
    port_name = port.get("name")
    if port_name in explicit_ports:
        requested = explicit_ports[port_name]
        if requested == AUTO_PORT:
            if allocator is None or channel is None:
                raise ValueError("automatic external port allocation requires a mux port range")
            return allocator.allocate(channel, port)
        return requested
    return validate_port_number(port.get("port"), f"spec.ports[{port_name}].port")


def process_channel_ports(channel, memo, service_factory=Service, allocator=None):
    """Return mux ports, annotation text, and the kr8s Service wrapper."""
    ports, annotation = resolve_channel_ports(channel, allocator=allocator)
    return ports, annotation, service_factory(channel)


def validate_declared_port_names(channel, explicit_ports=None):
    ports = channel["spec"].get("ports", [])
    unnamed_indexes = [str(index) for index, port in enumerate(ports) if not port.get("name")]
    if unnamed_indexes:
        raise ValueError(
            "channel Service ports must be named; missing name at spec.ports index(es): "
            + ", ".join(unnamed_indexes)
        )

    if explicit_ports is None:
        return

    declared_port_names = {port["name"] for port in ports}
    unknown_names = sorted(set(explicit_ports) - declared_port_names)
    if unknown_names:
        raise ValueError(
            f"{ANNOTATION_EXTERNAL_PORTS} references unknown port name(s): "
            + ", ".join(unknown_names)
        )


def resolve_channel_ports(channel, allocator=None):
    """Resolve desired mux ports for a channel without Kubernetes API access."""
    ports = set()
    ports_for_annotation = []
    annotations = channel.get("metadata", {}).get("annotations", {})
    explicit_ports = parse_external_ports_annotation(
        annotations.get(ANNOTATION_EXTERNAL_PORTS, "")
    )
    validate_declared_port_names(channel, explicit_ports)

    channel_ns = channel["metadata"]["namespace"]
    channel_name = channel["metadata"]["name"]

    for port in channel["spec"]["ports"]:
        mux_port = resolve_channel_external_port(
            port, explicit_ports, allocator=allocator, channel=channel
        )
        protocol = port.get("protocol", "TCP")
        port_name_hash = port_hash(channel_ns, channel_name, port["name"])
        ports.add(MuxPort(port_name_hash, mux_port, protocol))
        ports_for_annotation.append((port["name"], port["port"], mux_port))

    return ports, format_channel_port_annotation(ports_for_annotation)


def channel_port_claims(channel):
    annotations = channel.get("metadata", {}).get("annotations", {})
    explicit_ports = parse_external_ports_annotation(
        annotations.get(ANNOTATION_EXTERNAL_PORTS, "")
    )
    validate_declared_port_names(channel, explicit_ports)
    return explicit_ports


def collect_auto_allocation_keys(channels):
    keys = set()
    for channel in channels:
        try:
            explicit_ports = channel_port_claims(channel)
        except ValueError as error:
            logging.debug(
                "Skipping invalid channel while collecting auto allocation keys: %s",
                error,
            )
            continue
        for port in channel["spec"].get("ports", []):
            if explicit_ports.get(port.get("name")) == AUTO_PORT:
                keys.add(PortAllocationRef.from_channel_port(channel, port).key)
    return keys


def collect_static_port_claims(channels):
    """Collect non-auto desired mux port/protocol claims across channels."""
    claims = set()
    for channel in channels:
        try:
            explicit_ports = channel_port_claims(channel)
        except ValueError as error:
            logging.debug(
                "Skipping invalid channel while collecting static port claims: %s",
                error,
            )
            continue
        for port in channel["spec"].get("ports", []):
            if explicit_ports.get(port.get("name")) == AUTO_PORT:
                continue
            mux_port = resolve_channel_external_port(port, explicit_ports)
            claims.add((mux_port, port.get("protocol", "TCP")))
    return claims


def find_mux_port_conflicts(port_owners, channel, mux_ports):
    """Return conflict messages for mux ports already claimed by another channel."""
    channel_ns = channel["metadata"]["namespace"]
    channel_name = channel["metadata"]["name"]
    conflicts = []
    assignments = {}

    for mux_port in sorted(mux_ports, key=lambda item: (item.protocol, item.port, item.name)):
        key = (mux_port.port, mux_port.protocol)
        owner = f"{channel_ns}/{channel_name}:{mux_port.name}"
        previous_owner = port_owners.get(key) or assignments.get(key)
        if previous_owner and previous_owner != owner:
            conflicts.append(
                f"{mux_port.port}/{mux_port.protocol} is already claimed by {previous_owner}"
            )
            continue
        assignments[key] = owner

    if not conflicts:
        port_owners.update(assignments)

    return conflicts


def collect_channel_endpoints(channel, channel_service, memo):
    """Collect endpoint subsets from a channel Service for mux Endpoints."""
    endpoints = []
    channel_endpoints = memo.endpoints.get((channel_service.namespace, channel_service.name))

    if channel_endpoints and "subsets" in channel_endpoints:
        for subset in channel_endpoints["subsets"]:
            addresses = subset.get("addresses", [])
            not_ready_addresses = subset.get("notReadyAddresses", [])

            if not addresses and not not_ready_addresses:
                logging.debug(
                    "Skipping empty subset for channel %s/%s - no addresses or notReadyAddresses",
                    channel_service.namespace,
                    channel_service.name,
                )
                continue

            mux_subset = {"addresses": addresses}
            if not_ready_addresses:
                mux_subset["notReadyAddresses"] = not_ready_addresses

            for port in subset.get("ports", []):
                port_name = port_hash(
                    channel_service.namespace,
                    channel_service.name,
                    port["name"],
                )
                mux_subset.setdefault("ports", []).append(
                    {
                        "name": port_name,
                        "port": port["port"],
                        "protocol": port["protocol"],
                    }
                )
            endpoints.append(mux_subset)

    return endpoints


def channel_refs(channels):
    return [
        f"{channel['metadata']['namespace']}/{channel['metadata']['name']}"
        for channel in sorted(
            channels,
            key=lambda item: (item["metadata"]["namespace"], item["metadata"]["name"]),
        )
    ]


def build_generated_endpoints_metadata(mux_labels, mux_key, channels):
    """Build labels and annotations for controller-owned mux Endpoints."""
    labels = dict(mux_labels or {})
    labels.update(
        {
            "app.kubernetes.io/managed-by": "svc-lb-mux",
            "app.kubernetes.io/component": "mux-endpoints",
        }
    )
    annotations = {
        ANNOTATION_MANAGED: "true",
        annotation_key("mux"): f"{mux_key[0]}/{mux_key[1]}",
        ANNOTATION_CHANNELS: json_dumps(channel_refs(channels)),
    }
    return labels, annotations


def json_dumps(value):
    import json

    return json.dumps(value, sort_keys=True)


def update_channel_service_metadata(channel, channel_service, ports_annotation, status_lb):
    """Patch channel Service annotation and load balancer status when needed."""
    if (
        channel.get("metadata", {}).get("annotations", {}).get(ANNOTATION_PORTS)
        != ports_annotation
    ):
        if not DRYRUN_MODE:
            channel_service.patch(
                {"metadata": {"annotations": {ANNOTATION_PORTS: ports_annotation}}}
            )
        else:
            logging.debug(
                "[DRYRUN] Would patch channel %s/%s annotations",
                channel_service.namespace,
                channel_service.name,
            )

    if channel.get("status", {}).get("loadBalancer", {}) != status_lb:
        if not DRYRUN_MODE:
            channel_service.patch(
                {"status": {"loadBalancer": status_lb}}, subresource="status"
            )
        else:
            logging.debug(
                "[DRYRUN] Would patch channel %s/%s status",
                channel_service.namespace,
                channel_service.name,
            )


def get_old_endpoints_set(endpoint_raw):
    """Extract ready and not-ready endpoint tuples from an existing Endpoints object."""
    old_endpoints = set()
    old_not_ready_endpoints = set()

    if "subsets" in endpoint_raw:
        for subset in endpoint_raw["subsets"]:
            ready_ips = [address["ip"] for address in subset.get("addresses", [])]
            not_ready_ips = [
                address["ip"] for address in subset.get("notReadyAddresses", [])
            ]
            for port in subset.get("ports", []):
                for ip in ready_ips:
                    old_endpoints.add(MuxEp(ip, port["port"], port["protocol"]))
                for ip in not_ready_ips:
                    old_not_ready_endpoints.add(MuxEp(ip, port["port"], port["protocol"]))

    return old_endpoints, old_not_ready_endpoints


def get_current_endpoints_set(endpoint):
    """Extract ready and not-ready endpoint tuples from a desired Endpoints object."""
    current_endpoints = {
        MuxEp(address["ip"], port["port"], port["protocol"])
        for subset in endpoint["subsets"]
        for address in subset.get("addresses", [])
        for port in subset.get("ports", [])
    }
    current_not_ready_endpoints = {
        MuxEp(address["ip"], port["port"], port["protocol"])
        for subset in endpoint["subsets"]
        for address in subset.get("notReadyAddresses", [])
        for port in subset.get("ports", [])
    }
    return current_endpoints, current_not_ready_endpoints


def build_mux_ports(ports):
    mux_ports = [
        {
            "name": port.name,
            "port": port.port,
            "protocol": port.protocol,
        }
        for port in ports
    ]
    if mux_ports:
        return [port for port in mux_ports if port["name"] != "placeholder"]
    return [{"name": "placeholder", "port": 101, "protocol": "TCP"}]


def count_ready_channel_pods(channels, memo):
    return sum(
        len(subset.get("addresses", []))
        for channel_key in [
            (channel["metadata"]["namespace"], channel["metadata"]["name"])
            for channel in channels
        ]
        for channel_endpoints in [memo.endpoints.get(channel_key)]
        if channel_endpoints and "subsets" in channel_endpoints
        for subset in channel_endpoints["subsets"]
    )
