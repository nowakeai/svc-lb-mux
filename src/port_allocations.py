"""ConfigMap naming, encoding, and legacy auto-allocation helpers."""

import json
from dataclasses import dataclass
from hashlib import sha256

from kr8s.objects import ConfigMap

from config import (
    ANNOTATION_ALLOCATION_CONFIGMAP,
    ANNOTATION_PORT_RANGE,
    DRYRUN_MODE,
    annotation_key,
)

AUTO_PORT = "auto"
ALLOCATIONS_KEY = "allocations.json"
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PortAllocationRef:
    namespace: str
    service: str
    port_name: str
    protocol: str

    @classmethod
    def from_channel_port(cls, channel, port):
        return cls(
            namespace=channel["metadata"]["namespace"],
            service=channel["metadata"]["name"],
            port_name=port["name"],
            protocol=port.get("protocol", "TCP"),
        )

    @property
    def key(self):
        return f"{self.protocol}:{self.namespace}:{self.service}:{self.port_name}"


def parse_port_ranges(value: str):
    ranges = []
    if not value:
        return ranges

    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if "-" in item:
            start_text, end_text = item.split("-", 1)
        else:
            start_text = end_text = item
        start = _validate_port(start_text.strip(), ANNOTATION_PORT_RANGE)
        end = _validate_port(end_text.strip(), ANNOTATION_PORT_RANGE)
        if start > end:
            raise ValueError(
                f"Invalid {ANNOTATION_PORT_RANGE} range {item!r}; start is greater than end"
            )
        ranges.append((start, end))

    return ranges


def contains_port(ranges, port: int):
    return any(start <= port <= end for start, end in ranges)


def default_allocation_configmap_name(mux_name: str):
    name = f"{mux_name}-port-allocations"
    if len(name) <= 63:
        return name
    suffix = sha256(mux_name.encode()).hexdigest()[:8]
    return f"{mux_name[:54]}-{suffix}"


def allocation_configmap_name(mux):
    annotations = mux.annotations
    configured = annotations.get(ANNOTATION_ALLOCATION_CONFIGMAP)
    if configured:
        return configured
    return default_allocation_configmap_name(mux.name)


def requested_port_range(mux):
    return parse_port_ranges(mux.annotations.get(ANNOTATION_PORT_RANGE, ""))


class PortAllocator:
    def __init__(
        self,
        mux_key,
        ranges,
        state=None,
        reserved_ports=None,
        active_keys=None,
    ):
        self.mux_key = mux_key
        self.ranges = ranges
        self.allocations = _allocations_by_key(state or {})
        self.reserved_ports = set(reserved_ports or set())
        self.active_keys = set(active_keys or set())
        before = set(self.allocations)
        if active_keys is not None:
            for key in before - self.active_keys:
                del self.allocations[key]
        self.changed = set(self.allocations) != before

    def allocate(self, channel, port):
        if not self.ranges:
            raise ValueError(
                f"{ANNOTATION_PORT_RANGE} is required when external port is auto"
            )

        ref = PortAllocationRef.from_channel_port(channel, port)
        self.active_keys.add(ref.key)
        existing = self.allocations.get(ref.key)
        if existing:
            existing_port = int(existing["port"])
            if contains_port(self.ranges, existing_port) and not self._is_claimed(
                existing_port, ref.protocol, ref.key
            ):
                return existing_port

        allocated = self._next_available_port(ref.protocol)
        self.allocations[ref.key] = {
            "namespace": ref.namespace,
            "service": ref.service,
            "portName": ref.port_name,
            "protocol": ref.protocol,
            "port": allocated,
            "source": AUTO_PORT,
        }
        self.changed = True
        return allocated

    def prune_inactive(self):
        before = set(self.allocations)
        for key in before - self.active_keys:
            del self.allocations[key]
        if set(self.allocations) != before:
            self.changed = True

    def to_state(self):
        self.prune_inactive()
        return {
            "schemaVersion": SCHEMA_VERSION,
            "mux": {"namespace": self.mux_key[0], "name": self.mux_key[1]},
            "allocations": [
                self.allocations[key] for key in sorted(self.allocations)
            ],
        }

    def _next_available_port(self, protocol):
        used = self._used_port_protocols()
        for start, end in self.ranges:
            for port in range(start, end + 1):
                if (port, protocol) not in used:
                    return port
        raise ValueError(
            f"No available {protocol} port in {ANNOTATION_PORT_RANGE} "
            + _format_ranges(self.ranges)
        )

    def _is_claimed(self, port, protocol, current_key):
        for key, allocation in self.allocations.items():
            if key == current_key:
                continue
            if int(allocation["port"]) == port and allocation["protocol"] == protocol:
                return True
        return (port, protocol) in self.reserved_ports

    def _used_port_protocols(self):
        used = set(self.reserved_ports)
        for allocation in self.allocations.values():
            used.add((int(allocation["port"]), allocation["protocol"]))
        return used


class ConfigMapAllocationStore:
    def __init__(self, namespace, name, mux_key, configmap_factory=ConfigMap):
        self.namespace = namespace
        self.name = name
        self.mux_key = mux_key
        self.configmap_factory = configmap_factory
        self.configmap = self._new_configmap({})
        self.exists = False

    @property
    def mux_ref(self):
        return f"{self.mux_key[0]}/{self.mux_key[1]}"

    def load(self):
        if self.configmap.exists():
            self.configmap.refresh()
            self.exists = True
            self._validate_configmap_owner()
            state = decode_state(self.configmap.raw.get("data", {}))
            self._validate_state_owner(state)
            return state
        self.exists = False
        return {}

    def save(self, state):
        state = self._state_for_mux(state)
        data = encode_state(state)
        if DRYRUN_MODE:
            return
        metadata = {"annotations": {annotation_key("mux"): self.mux_ref}}
        if self.exists:
            self.configmap.patch({"metadata": metadata, "data": data})
        else:
            self.configmap = self._new_configmap(data)
            self.configmap.create()
            self.exists = True

    def _new_configmap(self, data):
        return self.configmap_factory(
            {
                "metadata": {
                    "name": self.name,
                    "namespace": self.namespace,
                    "labels": {
                        "app.kubernetes.io/name": "svc-lb-mux",
                        "app.kubernetes.io/component": "mux-state",
                    },
                    "annotations": {
                        annotation_key("mux"): self.mux_ref,
                    },
                },
                "data": data,
            }
        )

    def _state_for_mux(self, state):
        result = dict(state or {})
        result["schemaVersion"] = SCHEMA_VERSION
        result["mux"] = {"namespace": self.mux_key[0], "name": self.mux_key[1]}
        return result

    def _validate_configmap_owner(self):
        owner = (
            self.configmap.raw.get("metadata", {})
            .get("annotations", {})
            .get(annotation_key("mux"))
        )
        if owner and owner != self.mux_ref:
            raise ValueError(
                f"Mux state ConfigMap {self.namespace}/{self.name} is owned by mux {owner}; "
                f"expected {self.mux_ref}. Use one state ConfigMap per mux."
            )

    def _validate_state_owner(self, state):
        mux = state.get("mux") if state else None
        if not mux:
            return
        namespace = mux.get("namespace")
        name = mux.get("name")
        if (namespace, name) != self.mux_key:
            raise ValueError(
                f"Mux state ConfigMap {self.namespace}/{self.name} contains state for mux "
                f"{namespace}/{name}; expected {self.mux_ref}. Use one state ConfigMap per mux."
            )

def encode_state(state):
    return {ALLOCATIONS_KEY: json.dumps(state, indent=2, sort_keys=True)}


def decode_state(data):
    raw = data.get(ALLOCATIONS_KEY) if data else None
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("Invalid mux state ConfigMap JSON") from error


def _allocations_by_key(state):
    result = {}
    for allocation in state.get("allocations", []):
        ref = PortAllocationRef(
            namespace=allocation["namespace"],
            service=allocation["service"],
            port_name=allocation["portName"],
            protocol=allocation.get("protocol", "TCP"),
        )
        result[ref.key] = dict(allocation)
    return result


def _validate_port(value, field):
    try:
        port = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid {field} port {value!r}; expected integer") from error
    if not 1 <= port <= 65535:
        raise ValueError(f"Invalid {field} port {port}; expected 1-65535")
    return port


def _format_ranges(ranges):
    return ",".join(f"{start}-{end}" if start != end else str(start) for start, end in ranges)
