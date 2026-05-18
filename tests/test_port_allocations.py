import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from port_allocations import (
    ALLOCATIONS_KEY,
    ConfigMapAllocationStore,
    PortAllocationRef,
    PortAllocator,
    decode_state,
    default_allocation_configmap_name,
    allocation_configmap_name,
    encode_state,
    parse_port_ranges,
)


class PortAllocationTest(unittest.TestCase):
    def test_parse_port_ranges(self):
        self.assertEqual(
            parse_port_ranges("30000-30002, 31000"),
            [(30000, 30002), (31000, 31000)],
        )

    def test_parse_port_ranges_rejects_reversed_range(self):
        with self.assertRaises(ValueError):
            parse_port_ranges("30002-30000")

    def test_allocator_reuses_existing_assignment(self):
        state = {
            "allocations": [
                {
                    "namespace": "app",
                    "service": "api",
                    "portName": "http",
                    "protocol": "TCP",
                    "port": 30001,
                    "source": "auto",
                }
            ]
        }
        channel = channel_service()
        allocator = PortAllocator(
            ("svc-mux", "mux"),
            [(30000, 30002)],
            state,
            active_keys={
                PortAllocationRef.from_channel_port(
                    channel, channel["spec"]["ports"][0]
                ).key
            },
        )

        self.assertEqual(allocator.allocate(channel, channel["spec"]["ports"][0]), 30001)
        self.assertFalse(allocator.changed)

    def test_allocator_avoids_reserved_ports(self):
        channel = channel_service()
        allocator = PortAllocator(
            ("svc-mux", "mux"),
            [(30000, 30002)],
            reserved_ports={(30000, "TCP")},
        )

        self.assertEqual(allocator.allocate(channel, channel["spec"]["ports"][0]), 30001)
        self.assertTrue(allocator.changed)

    def test_allocator_allows_same_port_number_for_different_protocols(self):
        tcp = channel_service(name="dns-tcp", protocol="TCP")
        udp = channel_service(name="dns-udp", protocol="UDP")
        allocator = PortAllocator(("svc-mux", "mux"), [(30053, 30053)])

        self.assertEqual(allocator.allocate(tcp, tcp["spec"]["ports"][0]), 30053)
        self.assertEqual(allocator.allocate(udp, udp["spec"]["ports"][0]), 30053)

    def test_allocator_reallocates_existing_assignment_outside_active_range(self):
        state = {
            "allocations": [
                {
                    "namespace": "app",
                    "service": "api",
                    "portName": "http",
                    "protocol": "TCP",
                    "port": 32000,
                    "source": "auto",
                }
            ]
        }
        channel = channel_service()
        allocator = PortAllocator(
            ("svc-mux", "mux"),
            [(30000, 30000)],
            state,
            active_keys={
                PortAllocationRef.from_channel_port(
                    channel, channel["spec"]["ports"][0]
                ).key
            },
        )

        self.assertEqual(allocator.allocate(channel, channel["spec"]["ports"][0]), 30000)
        self.assertTrue(allocator.changed)

    def test_allocator_recovers_from_manual_duplicate_port_claim(self):
        state = {
            "allocations": [
                {
                    "namespace": "app",
                    "service": "api",
                    "portName": "http",
                    "protocol": "TCP",
                    "port": 30000,
                    "source": "auto",
                },
                {
                    "namespace": "app",
                    "service": "admin",
                    "portName": "http",
                    "protocol": "TCP",
                    "port": 30000,
                    "source": "auto",
                },
            ]
        }
        api = channel_service()
        admin = channel_service(name="admin")
        active_keys = {
            PortAllocationRef.from_channel_port(api, api["spec"]["ports"][0]).key,
            PortAllocationRef.from_channel_port(admin, admin["spec"]["ports"][0]).key,
        }
        allocator = PortAllocator(
            ("svc-mux", "mux"),
            [(30000, 30001)],
            state,
            active_keys=active_keys,
        )

        self.assertEqual(allocator.allocate(api, api["spec"]["ports"][0]), 30001)
        self.assertEqual(allocator.allocate(admin, admin["spec"]["ports"][0]), 30000)
        self.assertTrue(allocator.changed)

    def test_allocator_prunes_inactive_state_before_allocating(self):
        state = {
            "allocations": [
                {
                    "namespace": "old",
                    "service": "old",
                    "portName": "http",
                    "protocol": "TCP",
                    "port": 30000,
                    "source": "auto",
                }
            ]
        }
        channel = channel_service()
        allocator = PortAllocator(
            ("svc-mux", "mux"),
            [(30000, 30000)],
            state,
            active_keys={
                PortAllocationRef.from_channel_port(
                    channel, channel["spec"]["ports"][0]
                ).key
            },
        )

        self.assertEqual(allocator.allocate(channel, channel["spec"]["ports"][0]), 30000)
        self.assertTrue(allocator.changed)

    def test_allocator_prunes_deleted_channel_allocations_from_state(self):
        state = {
            "allocations": [
                {
                    "namespace": "app",
                    "service": "api",
                    "portName": "http",
                    "protocol": "TCP",
                    "port": 30000,
                    "source": "auto",
                }
            ]
        }
        allocator = PortAllocator(
            ("svc-mux", "mux"), [(30000, 30000)], state, active_keys=set()
        )

        self.assertEqual(allocator.to_state()["allocations"], [])
        self.assertTrue(allocator.changed)

    def test_allocator_treats_port_rename_as_new_identity(self):
        state = {
            "allocations": [
                {
                    "namespace": "app",
                    "service": "api",
                    "portName": "http",
                    "protocol": "TCP",
                    "port": 30000,
                    "source": "auto",
                }
            ]
        }
        renamed = channel_service(port_name="web")
        allocator = PortAllocator(
            ("svc-mux", "mux"),
            [(30000, 30000)],
            state,
            active_keys={
                PortAllocationRef.from_channel_port(
                    renamed, renamed["spec"]["ports"][0]
                ).key
            },
        )

        self.assertEqual(allocator.allocate(renamed, renamed["spec"]["ports"][0]), 30000)
        self.assertEqual(
            allocator.to_state()["allocations"][0]["portName"],
            "web",
        )
        self.assertTrue(allocator.changed)

    def test_allocator_reports_exhaustion(self):
        allocator = PortAllocator(
            ("svc-mux", "mux"),
            [(30000, 30000)],
            reserved_ports={(30000, "TCP")},
        )

        with self.assertRaises(ValueError):
            allocator.allocate(channel_service(), channel_service()["spec"]["ports"][0])

    def test_encode_decode_state_round_trip(self):
        state = {"schemaVersion": 1, "allocations": []}
        self.assertEqual(decode_state(encode_state(state)), state)

    def test_decode_state_rejects_invalid_json(self):
        with self.assertRaises(ValueError):
            decode_state({ALLOCATIONS_KEY: "not-json"})

    def test_default_configmap_name_is_dns_label_sized(self):
        name = default_allocation_configmap_name("m" * 80)
        self.assertLessEqual(len(name), 63)

    def test_store_saves_mux_owner_metadata_and_state(self):
        MemoryConfigMap.objects.clear()
        store = ConfigMapAllocationStore(
            "svc-mux",
            "mux-port-allocations",
            ("svc-mux", "mux"),
            configmap_factory=MemoryConfigMap,
        )

        store.save({"allocations": []})

        saved = MemoryConfigMap.objects[("svc-mux", "mux-port-allocations")]
        self.assertEqual(
            saved["metadata"]["annotations"]["svc-mux.nowake.ai/mux"],
            "svc-mux/mux",
        )
        self.assertEqual(
            decode_state(saved["data"])["mux"],
            {"namespace": "svc-mux", "name": "mux"},
        )

    def test_store_rejects_configmap_owned_by_another_mux(self):
        MemoryConfigMap.objects.clear()
        MemoryConfigMap.objects[("svc-mux", "shared-port-allocations")] = {
            "metadata": {
                "name": "shared-port-allocations",
                "namespace": "svc-mux",
                "annotations": {"svc-mux.nowake.ai/mux": "svc-mux/other"},
            },
            "data": encode_state(
                {
                    "schemaVersion": 1,
                    "mux": {"namespace": "svc-mux", "name": "other"},
                    "allocations": [],
                }
            ),
        }
        store = ConfigMapAllocationStore(
            "svc-mux",
            "shared-port-allocations",
            ("svc-mux", "mux"),
            configmap_factory=MemoryConfigMap,
        )

        with self.assertRaisesRegex(ValueError, "one allocation ConfigMap per mux"):
            store.load()

    def test_allocation_configmap_name_defaults_per_mux(self):
        first = mux_service("mux")
        second = mux_service("payments")

        self.assertEqual(allocation_configmap_name(first), "mux-port-allocations")
        self.assertEqual(
            allocation_configmap_name(second), "payments-port-allocations"
        )



class MemoryConfigMap:
    objects = {}

    def __init__(self, raw):
        self.raw = raw

    @property
    def key(self):
        metadata = self.raw["metadata"]
        return (metadata["namespace"], metadata["name"])

    def exists(self):
        if self.key in self.objects:
            self.raw = self._copy(self.objects[self.key])
            return True
        return False

    def refresh(self):
        self.raw = self._copy(self.objects[self.key])

    def patch(self, patch):
        current = self._copy(self.objects[self.key])
        current.setdefault("metadata", {}).setdefault("annotations", {}).update(
            patch.get("metadata", {}).get("annotations", {})
        )
        if "data" in patch:
            current["data"] = patch["data"]
        self.objects[self.key] = current
        self.raw = self._copy(current)

    def create(self):
        self.objects[self.key] = self._copy(self.raw)

    @staticmethod
    def _copy(value):
        import copy

        return copy.deepcopy(value)


def mux_service(name="mux", namespace="svc-mux", annotations=None):
    class Mux:
        pass

    mux = Mux()
    mux.name = name
    mux.namespace = namespace
    mux.annotations = annotations or {}
    return mux


def channel_service(name="api", namespace="app", port_name="http", protocol="TCP"):
    return {
        "metadata": {"namespace": namespace, "name": name},
        "spec": {"ports": [{"name": port_name, "port": 80, "protocol": protocol}]},
    }


if __name__ == "__main__":
    unittest.main()
