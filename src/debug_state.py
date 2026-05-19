"""Thread-safe state model for the debug API."""

import logging
import threading
from collections import deque
from datetime import datetime

from config import get_annotation

logger = logging.getLogger(__name__)

MAX_DEBUG_EVENTS = 100
MAX_EVENT_MESSAGE_LENGTH = 2000


class DebugStateStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._state = {
            "events": deque(maxlen=MAX_DEBUG_EVENTS),
            "mux_services": {},
            "channel_services": {},
            "endpoints": {},
        }

    def record_event(self, event_type: str, resource: str, message: str):
        with self._lock:
            message = truncate_event_message(message)
            now = datetime.now().isoformat()
            for existing in list(self._state["events"]):
                if (
                    existing["type"] == event_type
                    and existing["resource"] == resource
                    and existing["message"] == message
                ):
                    self._state["events"].remove(existing)
                    existing["timestamp"] = now
                    existing["last_timestamp"] = now
                    existing["count"] = existing.get("count", 1) + 1
                    self._state["events"].appendleft(existing)
                    return

            self._state["events"].appendleft(
                {
                    "timestamp": now,
                    "first_timestamp": now,
                    "last_timestamp": now,
                    "count": 1,
                    "type": event_type,
                    "resource": resource,
                    "message": message,
                }
            )

    def delete_mux_state(self, mux_key):
        with self._lock:
            if mux_key in self._state["mux_services"]:
                logger.debug(
                    "Removing deleted mux %s/%s from state",
                    mux_key[0],
                    mux_key[1],
                )
                del self._state["mux_services"][mux_key]

            self._state["endpoints"].pop(mux_key, None)

            channels_to_remove = [
                key
                for key, channel_data in self._state["channel_services"].items()
                if (
                    channel_data.get("mux_namespace") == mux_key[0]
                    and channel_data.get("mux_name") == mux_key[1]
                )
            ]

            for key in channels_to_remove:
                logger.debug(
                    "Removing channel %s/%s of deleted mux %s/%s",
                    key[0],
                    key[1],
                    mux_key[0],
                    mux_key[1],
                )
                del self._state["channel_services"][key]
                self._state["endpoints"].pop(key, None)

    def update_mux_state(
        self,
        memo=None,
        mux_channels=None,
        mux_key=None,
        mux_service_data=None,
    ):
        logger.debug(
            "update_mux_state called for mux %s/%s with service_data=%s",
            mux_key[0],
            mux_key[1],
            "present" if mux_service_data else "None",
        )

        with self._lock:
            self._update_mux_service(memo, mux_key, mux_service_data)
            self._update_endpoint_state(memo, mux_key, mux_key)
            self._update_mux_channels(memo, mux_channels, mux_key)

    def snapshot(self):
        with self._lock:
            return {
                "mux_services": {
                    f"{key[0]}/{key[1]}": dict(value)
                    for key, value in self._state["mux_services"].items()
                },
                "channel_services": {
                    f"{key[0]}/{key[1]}": dict(value)
                    for key, value in self._state["channel_services"].items()
                },
                "endpoints": {
                    f"{key[0]}/{key[1]}": dict(value)
                    for key, value in self._state["endpoints"].items()
                },
                "events": list(self._state["events"]),
            }

    def topology(self):
        topology = {}
        with self._lock:
            channel_services_snapshot = [
                dict(value) for value in self._state["channel_services"].values()
            ]
            mux_services_snapshot = {
                key: dict(value) for key, value in self._state["mux_services"].items()
            }

        for channel in channel_services_snapshot:
            mux_key = f"{channel['mux_namespace']}/{channel['mux_name']}"
            mux_tuple_key = (channel["mux_namespace"], channel["mux_name"])
            mux_exists = mux_tuple_key in mux_services_snapshot

            if mux_key not in topology:
                topology[mux_key] = self._topology_mux_entry(
                    mux_tuple_key,
                    mux_services_snapshot,
                    mux_exists,
                    channel,
                )

            topology[mux_key]["channels"].append(
                {
                    "namespace": channel["namespace"],
                    "name": channel["name"],
                    "external_dns": channel["external_dns"],
                    "custom_dns": channel.get("custom_dns"),
                    "ports": channel["ports"],
                }
            )

        return topology

    def _update_mux_service(self, memo, mux_key, mux_service_data):
        has_queue = mux_key in memo.mux_queues
        mux_data = {
            "namespace": mux_key[0],
            "name": mux_key[1],
            "has_queue": has_queue,
            "external_dns_hostname": None,
            "status_ingress": None,
        }

        if mux_service_data:
            annotations = mux_service_data.get("metadata", {}).get("annotations", {})
            external_dns = annotations.get("external-dns.alpha.kubernetes.io/hostname")
            status_lb = mux_service_data.get("status", {}).get("loadBalancer", {})
            ingress = status_lb.get("ingress", [])

            if ingress:
                mux_data["status_ingress"] = ingress[0].get("ip") or ingress[0].get(
                    "hostname"
                )
            mux_data["external_dns_hostname"] = external_dns

            if external_dns:
                logger.debug("Mux %s/%s external_dns: %s", mux_key[0], mux_key[1], external_dns)
            else:
                logger.warning("Mux %s/%s has no external_dns in annotations", mux_key[0], mux_key[1])

        self._state["mux_services"][mux_key] = mux_data

    def _update_mux_channels(self, memo, mux_channels, mux_key):
        channel_set = mux_channels.get(mux_key, set())
        mux_service_data = self._state["mux_services"].get(mux_key, {})
        mux_external_dns_hostname = mux_service_data.get("external_dns_hostname")
        mux_status_ingress = mux_service_data.get("status_ingress")
        current_channel_keys = set()

        for channel in channel_set:
            channel_key = (
                channel["metadata"]["namespace"],
                channel["metadata"]["name"],
            )
            current_channel_keys.add(channel_key)
            self._update_endpoint_state(memo, channel_key, channel_key)
            self._state["channel_services"][channel_key] = self._channel_state(
                channel,
                mux_key,
                mux_external_dns_hostname,
                mux_status_ingress,
            )

        channels_to_remove = [
            key
            for key, channel_data in self._state["channel_services"].items()
            if (
                channel_data.get("mux_namespace") == mux_key[0]
                and channel_data.get("mux_name") == mux_key[1]
                and key not in current_channel_keys
            )
        ]

        for key in channels_to_remove:
            logger.debug("Removing deleted channel %s/%s from state", key[0], key[1])
            del self._state["channel_services"][key]
            self._state["endpoints"].pop(key, None)

    def _update_endpoint_state(self, memo, endpoint_key, display_key):
        if endpoint_key not in memo.endpoints:
            return

        value = memo.endpoints[endpoint_key]
        pods = []
        for subset in value.get("subsets", []):
            for address in subset.get("addresses", []):
                if "ip" not in address:
                    continue
                target_ref = address.get("targetRef", {})
                if target_ref.get("kind") == "Pod":
                    pod_name = target_ref.get("name", address["ip"])
                    pod_ns = target_ref.get("namespace", display_key[0])
                    pods.append(f"{pod_ns}/{pod_name}")
                else:
                    pods.append(address["ip"])

        self._state["endpoints"][display_key] = {
            "namespace": display_key[0],
            "name": display_key[1],
            "ready_count": sum(
                len(subset.get("addresses", []))
                for subset in value.get("subsets", [])
            ),
            "not_ready_count": sum(
                len(subset.get("notReadyAddresses", []))
                for subset in value.get("subsets", [])
            ),
            "pods": pods,
        }

    def _channel_state(
        self,
        channel,
        mux_key,
        mux_external_dns_hostname,
        mux_status_ingress,
    ):
        channel_ns = channel["metadata"]["namespace"]
        channel_name = channel["metadata"]["name"]
        annotations = channel.get("metadata", {}).get("annotations", {})
        external_dns_annotation = annotations.get(
            "external-dns.alpha.kubernetes.io/hostname"
        )
        loadbalancer_ingress = (
            channel.get("status", {}).get("loadBalancer", {}).get("ingress", [])
        )
        channel_status_ingress = None
        if loadbalancer_ingress:
            channel_status_ingress = loadbalancer_ingress[0].get("ip") or loadbalancer_ingress[0].get(
                "hostname"
            )

        mux_port_map = parse_mux_port_annotation(get_annotation(annotations, "ports", ""))

        return {
            "namespace": channel_ns,
            "name": channel_name,
            "mux_namespace": mux_key[0],
            "mux_name": mux_key[1],
            "lb_class": channel["spec"].get("loadBalancerClass", ""),
            "external_dns": (
                external_dns_annotation
                or mux_external_dns_hostname
                or channel_status_ingress
                or mux_status_ingress
            ),
            "custom_dns": external_dns_annotation,
            "ports": [
                {
                    "name": port.get("name"),
                    "port": port.get("port"),
                    "node_port": port.get("nodePort"),
                    "protocol": port.get("protocol"),
                    "mux_port": mux_port_map.get(port.get("name")),
                }
                for port in channel["spec"].get("ports", [])
            ],
        }

    @staticmethod
    def _topology_mux_entry(mux_key, mux_services_snapshot, mux_exists, channel):
        if mux_exists:
            mux_info = mux_services_snapshot[mux_key]
            return {
                "mux_external_dns": mux_info.get("external_dns_hostname"),
                "mux_external_ip": mux_info.get("status_ingress"),
                "mux_missing": False,
                "has_queue": mux_info.get("has_queue", False),
                "channels": [],
            }

        logger.warning(
            "Channel %s/%s references non-existent mux %s/%s",
            channel["namespace"],
            channel["name"],
            channel["mux_namespace"],
            channel["mux_name"],
        )
        return {
            "mux_external_dns": None,
            "mux_external_ip": None,
            "mux_missing": True,
            "has_queue": False,
            "channels": [],
        }


def parse_mux_port_annotation(mux_ports_annotation: str):
    mux_port_map = {}
    if not mux_ports_annotation:
        return mux_port_map

    for mapping in mux_ports_annotation.split(","):
        mapping = mapping.strip()
        if ":" not in mapping:
            continue
        port_name, port_spec = mapping.split(":", 1)
        if "->" in port_spec:
            _, mux_port = port_spec.split("->", 1)
        else:
            mux_port = port_spec
        mux_port_map[port_name.strip()] = int(mux_port.strip())
    return mux_port_map


state_store = DebugStateStore()


def record_event(event_type: str, resource: str, message: str):
    state_store.record_event(event_type, resource, message)


def truncate_event_message(message: str) -> str:
    if len(message) <= MAX_EVENT_MESSAGE_LENGTH:
        return message
    omitted = len(message) - MAX_EVENT_MESSAGE_LENGTH
    return f"{message[:MAX_EVENT_MESSAGE_LENGTH]}... [truncated {omitted} chars]"


def delete_mux_state(mux_key):
    state_store.delete_mux_state(mux_key)


def update_mux_state(memo=None, mux_channels=None, mux_key=None, mux_service_data=None):
    state_store.update_mux_state(memo, mux_channels, mux_key, mux_service_data)
