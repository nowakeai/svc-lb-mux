"""Runtime configuration for Service LoadBalancer Multiplexer."""

import os

DEFAULT_API_PREFIX = "svc-mux.nowake.ai"
GKE_SERVICE_LOADBALANCER_MAX_PORTS = 100
GKE_LOAD_BALANCER_CLASS_PREFIX = "networking.gke.io/"
GKE_PROVIDER_ANNOTATIONS = (
    "cloud.google.com/l4-rbs",
    "networking.gke.io/load-balancer-type",
    "networking.gke.io/load-balancer-ip-addresses",
)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes", "on")


NAMESPACE = os.environ.get("NAMESPACE", "default")
DEFAULT_MUX_NAMESPACE = os.environ.get("DEFAULT_MUX_NAMESPACE", NAMESPACE)
POD_NAME = os.environ.get("POD_NAME", "svc-lb-mux")

DEBUG_WEB_ENABLED = env_bool("DEBUG_WEB_ENABLED", True)
DEBUG_WEB_ACTIONS_ENABLED = env_bool("DEBUG_WEB_ACTIONS_ENABLED", False)
DEBUG_WEB_PORT = int(os.environ.get("DEBUG_WEB_PORT", "8080"))

DRYRUN_MODE = env_bool("DRYRUN_MODE", False)

API_PREFIX = os.environ.get("API_PREFIX", DEFAULT_API_PREFIX).strip() or DEFAULT_API_PREFIX

DAEMON_QUEUE_TIMEOUT = 10

ANNOTATION_MULTIPLEXER = f"{API_PREFIX}/multiplexer"
ANNOTATION_DISABLED = f"{API_PREFIX}/disabled"
ANNOTATION_PORTS = f"{API_PREFIX}/ports"
ANNOTATION_EXTERNAL_PORTS = f"{API_PREFIX}/external-ports"
ANNOTATION_PORT_RANGE = f"{API_PREFIX}/port-range"
ANNOTATION_MAX_PORTS = f"{API_PREFIX}/max-ports"
ANNOTATION_ALLOCATION_CONFIGMAP = f"{API_PREFIX}/allocation-configmap"
ANNOTATION_CHANNELS = f"{API_PREFIX}/channels"
ANNOTATION_TOPOLOGY = f"{API_PREFIX}/topology"
ANNOTATION_SUMMARY = f"{API_PREFIX}/summary"
ANNOTATION_MANAGED = f"{API_PREFIX}/managed"
FINALIZER = f"{API_PREFIX}/finalizer"


def annotation_key(name: str, prefix: str = API_PREFIX) -> str:
    return f"{prefix}/{name}"


def get_annotation(annotations: dict, name: str, default=None):
    return annotations.get(annotation_key(name), default)
