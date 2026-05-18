"""Utility functions for LB4 Multiplexer"""


def parse_external_dns(dns_annotation):
    """Parse external DNS annotation which may contain multiple hostnames.

    The external-dns.alpha.kubernetes.io/hostname annotation can contain
    multiple comma-separated hostnames, for example:
    "example.com,www.example.com,api.example.com"

    Args:
        dns_annotation: String from external-dns.alpha.kubernetes.io/hostname annotation
                       May be single hostname or comma-separated list

    Returns:
        list: List of hostnames, or empty list if annotation is None/empty

    Examples:
        >>> parse_external_dns("example.com")
        ['example.com']
        >>> parse_external_dns("example.com, www.example.com")
        ['example.com', 'www.example.com']
        >>> parse_external_dns(None)
        []
    """
    if not dns_annotation:
        return []
    # Split by comma and strip whitespace
    return [h.strip() for h in dns_annotation.split(",") if h.strip()]


def get_primary_dns(dns_annotation):
    """Get the primary (first) DNS hostname from annotation.

    Args:
        dns_annotation: String from external-dns.alpha.kubernetes.io/hostname annotation

    Returns:
        str: Primary hostname or None if annotation is empty

    Examples:
        >>> get_primary_dns("example.com,www.example.com")
        'example.com'
        >>> get_primary_dns("example.com")
        'example.com'
        >>> get_primary_dns(None)
        None
    """
    hostnames = parse_external_dns(dns_annotation)
    return hostnames[0] if hostnames else None


def format_dns_display(dns_annotation):
    """Format DNS annotation for display, showing primary + count if multiple.

    Args:
        dns_annotation: String from external-dns.alpha.kubernetes.io/hostname annotation

    Returns:
        str: Formatted string like "example.com (+2 more)" or "example.com"

    Examples:
        >>> format_dns_display("example.com")
        'example.com'
        >>> format_dns_display("example.com,www.example.com,api.example.com")
        'example.com (+2 more)'
        >>> format_dns_display(None)
        None
    """
    if not dns_annotation:
        return None

    dns_list = parse_external_dns(dns_annotation)
    if not dns_list:
        return None

    if len(dns_list) > 1:
        return f"{dns_list[0]} (+{len(dns_list)-1} more)"
    else:
        return dns_list[0]
