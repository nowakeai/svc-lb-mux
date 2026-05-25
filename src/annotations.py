"""Human-readable annotation formatting helpers."""

from config import ANNOTATION_EXTERNAL_DNS_HOSTNAME, ANNOTATION_PORTS
import utils


def format_topology_annotation(channels, memo, mux_external_dns=None):
    """Generate a readable topology summary for a mux Service annotation."""
    lines = []
    total_ports = 0
    total_ready_pods = 0

    if mux_external_dns:
        mux_dns_display = utils.format_dns_display(mux_external_dns)
        lines.append(f"Mux DNS: {mux_dns_display or mux_external_dns}")
        lines.append("")

    lines.append("Channels:")
    for ch in sorted(
        channels,
        key=lambda c: (c["metadata"]["namespace"], c["metadata"]["name"]),
    ):
        ch_ns = ch["metadata"]["namespace"]
        ch_name = ch["metadata"]["name"]
        ch_key = (ch_ns, ch_name)

        ch_annotations = ch.get("metadata", {}).get("annotations", {})
        ch_dns_annotation = ch_annotations.get(ANNOTATION_EXTERNAL_DNS_HOSTNAME)
        custom_dns_marker = " (custom)" if ch_dns_annotation else ""

        if ch_dns_annotation:
            ch_dns_display = (
                utils.format_dns_display(ch_dns_annotation) or ch_dns_annotation
            )
        else:
            ch_dns_display = utils.get_primary_dns(mux_external_dns) or "pending"

        lines.append(f"  - {ch_ns}/{ch_name}")
        lines.append(f"    DNS: {ch_dns_display}{custom_dns_marker}")

        chep = memo.endpoints.get(ch_key)
        ready_pods_count = 0
        if chep and "subsets" in chep:
            for subset in chep["subsets"]:
                ready_pods_count += len(subset.get("addresses", []))

        mux_port_mappings = parse_port_mappings(ch_annotations.get(ANNOTATION_PORTS, ""))
        ports_line = "    Ports:"
        for p in ch["spec"].get("ports", []):
            port_name = p.get("name", "unnamed")
            channel_port = p.get("port")
            mux_port = mux_port_mappings.get(port_name, p.get("nodePort", "pending"))
            ports_line += f" {port_name}:{channel_port}->{mux_port}"
            total_ports += 1

        lines.append(ports_line)
        lines.append(f"    Backend: {ready_pods_count} pod(s) ready")
        total_ready_pods += ready_pods_count

    lines.append("")
    lines.append(
        f"Summary: {len(channels)} channel(s), {total_ports} port(s), "
        f"{total_ready_pods} backend pod(s)"
    )

    return "\n".join(lines)


def format_summary_annotation(channels, total_ports, total_pods, mux_external_dns=None):
    """Generate a one-line mux summary annotation."""
    dns_display = utils.format_dns_display(mux_external_dns)
    dns_part = f"DNS: {dns_display}" if dns_display else "DNS: pending"

    return (
        f"{len(channels)} channel(s) | {total_ports} port(s) | "
        f"{total_pods} pod(s) | {dns_part}"
    )


def format_channel_port_annotation(ports_list):
    """Format channel-to-mux port mappings for a channel Service annotation."""
    return ", ".join(
        f"{name}:{channel_port}->{mux_port}"
        for name, channel_port, mux_port in ports_list
    )


def parse_port_mappings(value: str):
    """Parse a channel port annotation into {port_name: mux_port}."""
    mappings = {}
    if not value:
        return mappings

    for item in value.split(","):
        item = item.strip()
        if not item or ":" not in item or "->" not in item:
            continue
        port_name, rest = item.split(":", 1)
        _, mux_port = rest.split("->", 1)
        mappings[port_name.strip()] = mux_port.strip()

    return mappings
