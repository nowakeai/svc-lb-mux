"""Kubernetes object reference parsing helpers."""

import re

from config import API_PREFIX, DEFAULT_MUX_NAMESPACE

DNS_LABEL_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


def validate_dns_label(value: str, field: str):
    if not value or len(value) > 63 or not DNS_LABEL_RE.match(value):
        raise ValueError(f"Invalid {field} {value!r}; expected a Kubernetes DNS label")


def get_mux_from_lb_class(
    cls: str,
    api_prefix: str = API_PREFIX,
    default_mux_namespace: str = DEFAULT_MUX_NAMESPACE,
):
    """Parse loadBalancerClass as <api-prefix>/<name>[.<namespace>]."""
    prefix = f"{api_prefix}/"
    if not cls.startswith(prefix):
        expected = f"{api_prefix}/<name>[.<namespace>]"
        raise ValueError(f"Invalid LoadBalancerClass, expected format: {expected}")

    mux_ref = cls[len(prefix) :]
    split_parts = mux_ref.split(".")
    if len(split_parts) == 1:
        mux_name = split_parts[0]
        mux_ns = default_mux_namespace
    elif len(split_parts) == 2:
        mux_name, mux_ns = split_parts
    else:
        raise ValueError(
            f"Invalid LoadBalancerClass {cls!r}; expected {api_prefix}/<name>[.<namespace>]"
        )

    validate_dns_label(mux_name, "multiplexer service name")
    validate_dns_label(mux_ns, "multiplexer namespace")
    return mux_ns, mux_name
