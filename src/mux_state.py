"""Per-mux medium-term state backed by the mux state ConfigMap."""

from hashlib import sha256

from annotations import parse_port_mappings
from config import ANNOTATION_EXTERNAL_PORTS, ANNOTATION_PORTS
from port_allocations import AUTO_PORT, PortAllocationRef, contains_port

PORT_CLAIMS_KEY = "portClaims"
MODE_AUTO = AUTO_PORT
MODE_STATIC = "static"


class MuxState:
    """Manage persisted port claims for one mux reconciliation pass."""

    def __init__(
        self,
        mux_key,
        ranges=None,
        state=None,
        channels=None,
        reserved_ports=None,
        reserved_port_owners=None,
    ):
        self.mux_key = mux_key
        self.ranges = ranges or []
        self.active_keys = _active_claim_keys(channels or [])
        self.reserved_ports = set(reserved_ports or set())
        self.reserved_port_owners = dict(reserved_port_owners or {})
        self.claims = _claims_by_key(state or {})
        self.changed = _state_needs_claim_write(state or {})
        self._prune_inactive()

    def port_owners(self):
        owners = {}
        for key, claim in self.claims.items():
            port = _claim_mux_port(claim)
            if port is None:
                continue
            protocol = claim.get("protocol", "TCP")
            owners.setdefault((port, protocol), _claim_owner(claim))
        return owners

    def allocate(self, channel, port):
        """Allocate or reuse a mux port for external-ports: name:auto."""
        if not self.ranges:
            raise ValueError(
                f"{ANNOTATION_EXTERNAL_PORTS} automatic allocation requires a mux port range"
            )

        ref = PortAllocationRef.from_channel_port(channel, port)
        owner = _ref_owner(ref)
        existing = self.claims.get(ref.key)
        if existing:
            existing_port = _claim_mux_port(existing)
            if (
                existing_port is not None
                and contains_port(self.ranges, existing_port)
                and not self._is_claimed(existing_port, ref.protocol, ref.key, owner)
            ):
                return existing_port

        allocated = self._next_available_port(ref.protocol, owner)
        self._set_claim(ref, channel_port=port.get("port"), mux_port=allocated, mode=MODE_AUTO)
        return allocated

    def record_channel_claims(self, channel, ports_annotation):
        """Persist accepted claims for a successfully reconciled channel."""
        mux_port_mappings = parse_port_mappings(ports_annotation)
        explicit_modes = _external_port_modes(
            channel.get("metadata", {}).get("annotations", {}).get(ANNOTATION_EXTERNAL_PORTS, "")
        )

        for port in channel.get("spec", {}).get("ports", []):
            port_name = port.get("name")
            if not port_name or port_name not in mux_port_mappings:
                continue
            try:
                mux_port = _validate_port_number(mux_port_mappings[port_name], ANNOTATION_PORTS)
            except ValueError:
                continue
            mode = MODE_AUTO if explicit_modes.get(port_name) == MODE_AUTO else MODE_STATIC
            ref = PortAllocationRef.from_channel_port(channel, port)
            self._set_claim(ref, channel_port=port.get("port"), mux_port=mux_port, mode=mode)

    def to_state(self):
        claims = [self.claims[key] for key in sorted(self.claims)]
        return {
            "schemaVersion": 1,
            "mux": {"namespace": self.mux_key[0], "name": self.mux_key[1]},
            PORT_CLAIMS_KEY: claims,
            "allocations": [claim for claim in claims if claim.get("source") == MODE_AUTO],
        }

    def _set_claim(self, ref, channel_port, mux_port, mode):
        claim = {
            "namespace": ref.namespace,
            "service": ref.service,
            "portName": ref.port_name,
            "protocol": ref.protocol,
            "channelPort": channel_port,
            "muxPort": mux_port,
            "port": mux_port,
            "source": mode,
        }
        if self.claims.get(ref.key) != claim:
            self.claims[ref.key] = claim
            self.changed = True

    def _prune_inactive(self):
        before = set(self.claims)
        for key in before - self.active_keys:
            del self.claims[key]
        if set(self.claims) != before:
            self.changed = True

    def _next_available_port(self, protocol, owner):
        used = self._used_port_protocols(owner)
        for start, end in self.ranges:
            for port in range(start, end + 1):
                if (port, protocol) not in used:
                    return port
        ranges = ",".join(
            f"{start}-{end}" if start != end else str(start)
            for start, end in self.ranges
        )
        raise ValueError(
            f"No available {protocol} port in mux port range {ranges}"
        )

    def _is_claimed(self, port, protocol, current_key, owner):
        port_key = (port, protocol)
        for key, claim in self.claims.items():
            if key == current_key:
                continue
            if _claim_mux_port(claim) == port and claim.get("protocol", "TCP") == protocol:
                return True
        if port_key in self.reserved_ports:
            return True
        reserved_owner = self.reserved_port_owners.get(port_key)
        return reserved_owner is not None and reserved_owner != owner

    def _used_port_protocols(self, owner):
        used = set(self.reserved_ports)
        for port_key, reserved_owner in self.reserved_port_owners.items():
            if reserved_owner != owner:
                used.add(port_key)
        for claim in self.claims.values():
            port = _claim_mux_port(claim)
            if port is not None:
                used.add((port, claim.get("protocol", "TCP")))
        return used


def _claims_by_key(state):
    claims = {}
    for claim in _state_claims(state):
        ref = PortAllocationRef(
            namespace=claim["namespace"],
            service=claim["service"],
            port_name=claim["portName"],
            protocol=claim.get("protocol", "TCP"),
        )
        normalized = dict(claim)
        mux_port = _claim_mux_port(normalized)
        if mux_port is not None:
            normalized["muxPort"] = mux_port
            normalized["port"] = mux_port
        normalized.setdefault("source", MODE_AUTO)
        claims[ref.key] = normalized
    return claims


def _state_claims(state):
    if state.get(PORT_CLAIMS_KEY) is not None:
        return state.get(PORT_CLAIMS_KEY, [])
    return state.get("allocations", [])


def _state_needs_claim_write(state):
    return bool(state) and state.get(PORT_CLAIMS_KEY) is None


def _active_claim_keys(channels):
    keys = set()
    for channel in channels:
        for port in channel.get("spec", {}).get("ports", []):
            if not port.get("name"):
                continue
            keys.add(PortAllocationRef.from_channel_port(channel, port).key)
    return keys


def _claim_mux_port(claim):
    value = claim.get("muxPort", claim.get("port"))
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _claim_owner(claim):
    mux_name = _claim_mux_name(claim)
    return f"{claim['namespace']}/{claim['service']}:{mux_name}"


def _ref_owner(ref):
    claim = {
        "namespace": ref.namespace,
        "service": ref.service,
        "portName": ref.port_name,
    }
    return _claim_owner(claim)


def _claim_mux_name(claim):
    return sha256(
        f"{claim['namespace']}/{claim['service']}/{claim['portName']}".encode()
    ).hexdigest()[:7]


def _validate_port_number(value, field):
    try:
        port = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid {field} port {value!r}; expected integer") from error
    if not 1 <= port <= 65535:
        raise ValueError(f"Invalid {field} port {port}; expected 1-65535")
    return port


def _external_port_modes(value):
    modes = {}
    if not value:
        return modes
    for item in value.split(","):
        item = item.strip()
        if not item or ":" not in item:
            continue
        name, port_text = item.split(":", 1)
        if port_text.strip().lower() == MODE_AUTO:
            modes[name.strip()] = MODE_AUTO
    return modes
