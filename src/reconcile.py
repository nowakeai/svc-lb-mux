"""Pure and Kubernetes-facing helpers for mux reconciliation."""

import logging
from hashlib import sha256

from kr8s.objects import Service

from annotations import format_channel_port_annotation, parse_port_mappings
from utils import parse_external_dns
from config import (
    ANNOTATION_CHANNELS,
    ANNOTATION_MANAGED,
    AGGREGATED_EXTERNAL_DNS_ANNOTATIONS,
    ANNOTATION_EXTERNAL_DNS_AGGREGATED,
    ANNOTATION_EXTERNAL_DNS_HOSTNAME,
    ANNOTATION_EXTERNAL_PORTS,
    ANNOTATION_MAX_PORTS,
    ANNOTATION_PORTS,
    DRYRUN_MODE,
    GKE_LOAD_BALANCER_CLASS_PREFIX,
    EXTERNAL_DNS_CLOUDFLARE_PROXIED_ANNOTATION,
    EXTERNAL_DNS_HOSTNAME_ANNOTATION,
    GKE_PROVIDER_ANNOTATIONS,
    GKE_SERVICE_LOADBALANCER_MAX_PORTS,
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


def parse_aggregated_external_dns_keys(value):
    """Parse the mux annotation that tracks controller-owned external-dns keys."""
    if not value:
        return set()
    return {item.strip() for item in value.split(",") if item.strip()}


def format_aggregated_external_dns_keys(keys):
    """Format controller-owned external-dns keys in a stable order."""
    ordered = [key for key in AGGREGATED_EXTERNAL_DNS_ANNOTATIONS if key in keys]
    return ",".join(ordered)


def channel_external_dns_hostname(channel):
    """Return the svc-lb-mux channel DNS annotation used for mux aggregation."""
    annotations = channel.get("metadata", {}).get("annotations", {}) or {}
    return annotations.get(ANNOTATION_EXTERNAL_DNS_HOSTNAME)


def aggregate_external_dns_annotations(channels):
    """Build external-dns annotations that should be published on the mux."""
    hostnames = []
    seen_hostnames = set()
    saw_cloudflare_proxied = False
    cloudflare_proxied = True

    for channel in sorted(
        channels,
        key=lambda item: (item["metadata"]["namespace"], item["metadata"]["name"]),
    ):
        annotations = channel.get("metadata", {}).get("annotations", {}) or {}

        for hostname in parse_external_dns(channel_external_dns_hostname(channel)):
            if hostname in seen_hostnames:
                continue
            seen_hostnames.add(hostname)
            hostnames.append(hostname)

        if EXTERNAL_DNS_CLOUDFLARE_PROXIED_ANNOTATION not in annotations:
            continue
        proxied_value = str(
            annotations[EXTERNAL_DNS_CLOUDFLARE_PROXIED_ANNOTATION]
        ).strip().lower()
        if proxied_value not in ("true", "false"):
            continue
        saw_cloudflare_proxied = True
        cloudflare_proxied = cloudflare_proxied and proxied_value == "true"

    aggregated = {}
    if hostnames:
        aggregated[EXTERNAL_DNS_HOSTNAME_ANNOTATION] = ",".join(hostnames)
    if saw_cloudflare_proxied:
        aggregated[EXTERNAL_DNS_CLOUDFLARE_PROXIED_ANNOTATION] = str(
            cloudflare_proxied
        ).lower()
    return aggregated


def external_dns_aggregation_conflicts(mux_annotations, channels):
    """Return channel annotations that cannot be aggregated due to mux ownership."""
    mux_annotations = mux_annotations or {}
    managed_keys = parse_aggregated_external_dns_keys(
        mux_annotations.get(ANNOTATION_EXTERNAL_DNS_AGGREGATED)
    )
    hostname_user_owned = (
        EXTERNAL_DNS_HOSTNAME_ANNOTATION in mux_annotations
        and EXTERNAL_DNS_HOSTNAME_ANNOTATION not in managed_keys
    )
    cloudflare_user_owned = (
        EXTERNAL_DNS_CLOUDFLARE_PROXIED_ANNOTATION in mux_annotations
        and EXTERNAL_DNS_CLOUDFLARE_PROXIED_ANNOTATION not in managed_keys
    )

    conflicts = []
    for channel in sorted(
        channels,
        key=lambda item: (item["metadata"]["namespace"], item["metadata"]["name"]),
    ):
        annotations = channel.get("metadata", {}).get("annotations", {}) or {}
        if hostname_user_owned and annotations.get(ANNOTATION_EXTERNAL_DNS_HOSTNAME):
            conflicts.append(
                (
                    channel,
                    ANNOTATION_EXTERNAL_DNS_HOSTNAME,
                    EXTERNAL_DNS_HOSTNAME_ANNOTATION,
                )
            )
        if (
            cloudflare_user_owned
            and EXTERNAL_DNS_CLOUDFLARE_PROXIED_ANNOTATION in annotations
        ):
            conflicts.append(
                (
                    channel,
                    EXTERNAL_DNS_CLOUDFLARE_PROXIED_ANNOTATION,
                    EXTERNAL_DNS_CLOUDFLARE_PROXIED_ANNOTATION,
                )
            )
    return conflicts


def apply_aggregated_external_dns_annotations(annotations, aggregated):
    """Return mux annotations with controller-owned external-dns keys applied."""
    desired = dict(annotations or {})
    managed_keys = parse_aggregated_external_dns_keys(
        desired.get(ANNOTATION_EXTERNAL_DNS_AGGREGATED)
    )
    next_managed_keys = set()

    for key in AGGREGATED_EXTERNAL_DNS_ANNOTATIONS:
        user_owned = key in desired and key not in managed_keys
        if user_owned:
            continue
        if key in aggregated:
            desired[key] = aggregated[key]
            next_managed_keys.add(key)
        else:
            desired.pop(key, None)

    if next_managed_keys:
        desired[ANNOTATION_EXTERNAL_DNS_AGGREGATED] = (
            format_aggregated_external_dns_keys(next_managed_keys)
        )
    else:
        desired.pop(ANNOTATION_EXTERNAL_DNS_AGGREGATED, None)
    return desired


def validate_port_number(value, field: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid {field} port {value!r}; expected integer") from error

    if not 1 <= port <= 65535:
        raise ValueError(f"Invalid {field} port {port}; expected 1-65535")
    return port


def requested_max_ports(mux):
    value = mux.annotations.get(ANNOTATION_MAX_PORTS, "")
    if value in (None, ""):
        return None
    try:
        max_ports = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid {ANNOTATION_MAX_PORTS} value {value!r}; expected integer") from error
    if max_ports < 1:
        raise ValueError(f"Invalid {ANNOTATION_MAX_PORTS} value {max_ports}; expected >= 1")
    return max_ports


def _object_get(value, key, default=None):
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def is_gke_load_balancer_service(mux):
    spec = _object_get(mux, "spec", {})
    load_balancer_class = _object_get(spec, "loadBalancerClass", "") or ""
    if str(load_balancer_class).startswith(GKE_LOAD_BALANCER_CLASS_PREFIX):
        return True

    annotations = _object_get(mux, "annotations", {}) or {}
    return any(key in annotations for key in GKE_PROVIDER_ANNOTATIONS)


def effective_mux_max_ports(mux):
    max_ports = requested_max_ports(mux)
    if not is_gke_load_balancer_service(mux):
        return max_ports
    if max_ports is None or max_ports > GKE_SERVICE_LOADBALANCER_MAX_PORTS:
        return GKE_SERVICE_LOADBALANCER_MAX_PORTS
    return max_ports


def gke_max_ports_warning(mux):
    if not is_gke_load_balancer_service(mux):
        return None

    configured = requested_max_ports(mux)
    if configured is None:
        return (
            "Detected a GKE LoadBalancer mux without "
            f"{ANNOTATION_MAX_PORTS}; applying the GKE Service LoadBalancer "
            f"limit of {GKE_SERVICE_LOADBALANCER_MAX_PORTS} ports"
        )
    if configured > GKE_SERVICE_LOADBALANCER_MAX_PORTS:
        return (
            f"Detected a GKE LoadBalancer mux with {ANNOTATION_MAX_PORTS}={configured}; "
            f"using the GKE Service LoadBalancer limit of "
            f"{GKE_SERVICE_LOADBALANCER_MAX_PORTS} ports"
        )
    return None


def channel_declared_port_count(channel):
    return len(channel.get("spec", {}).get("ports", []))


def would_exceed_mux_port_limit(current_ports, channel, max_ports):
    if max_ports is None:
        return False
    return len(current_ports) + channel_declared_port_count(channel) > max_ports


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


def collect_existing_port_owners(channels, existing_ports):
    """Return owners derived from already-applied mux and channel state.

    The mux Service is the strongest source because it describes live provider
    ports. Channel mapping annotations are a fallback for cases where another
    reconciler temporarily wrote the mux Service back to a placeholder port.
    """
    owners_by_mux_name = _channel_owners_by_mux_name(channels)
    port_owners = _owners_from_existing_mux_ports(owners_by_mux_name, existing_ports)

    annotation_owners = _owners_from_channel_port_annotations(channels)
    for key, candidates in annotation_owners.items():
        if key in port_owners or len(candidates) != 1:
            continue
        port_owners[key] = next(iter(candidates))

    return port_owners


def _channel_owners_by_mux_name(channels):
    owners_by_mux_name = {}
    for channel in channels:
        channel_ns = channel["metadata"]["namespace"]
        channel_name = channel["metadata"]["name"]
        for port in channel.get("spec", {}).get("ports", []):
            port_name = port.get("name")
            if not port_name:
                continue
            mux_name = port_hash(channel_ns, channel_name, port_name)
            owners_by_mux_name.setdefault(mux_name, set()).add(
                f"{channel_ns}/{channel_name}:{mux_name}"
            )
    return owners_by_mux_name


def _owners_from_existing_mux_ports(owners_by_mux_name, existing_ports):
    port_owners = {}
    for existing_port in existing_ports:
        candidates = owners_by_mux_name.get(existing_port.name, set())
        if len(candidates) != 1:
            continue
        key = (existing_port.port, existing_port.protocol)
        port_owners.setdefault(key, next(iter(candidates)))
    return port_owners


def _owners_from_channel_port_annotations(channels):
    annotation_owners = {}
    for channel in channels:
        annotations = channel.get("metadata", {}).get("annotations", {})
        mux_port_mappings = parse_port_mappings(annotations.get(ANNOTATION_PORTS, ""))
        if not mux_port_mappings:
            continue

        channel_ns = channel["metadata"]["namespace"]
        channel_name = channel["metadata"]["name"]
        for port in channel.get("spec", {}).get("ports", []):
            port_name = port.get("name")
            if not port_name or port_name not in mux_port_mappings:
                continue
            try:
                mux_port = validate_port_number(
                    mux_port_mappings[port_name], ANNOTATION_PORTS
                )
            except ValueError:
                continue
            mux_name = port_hash(channel_ns, channel_name, port_name)
            key = (mux_port, port.get("protocol", "TCP"))
            annotation_owners.setdefault(key, set()).add(
                f"{channel_ns}/{channel_name}:{mux_name}"
            )
    return annotation_owners


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
