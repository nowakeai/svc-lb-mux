import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from debug_state import DebugStateStore, parse_mux_port_annotation


class DebugStateTest(unittest.TestCase):
    def test_parse_mux_port_annotation_supports_mapping_formats(self):
        self.assertEqual(
            parse_mux_port_annotation("http:80->30080, grpc:30090"),
            {"http": 30080, "grpc": 30090},
        )

    def test_update_mux_state_builds_state_and_topology(self):
        store = DebugStateStore()
        memo = SimpleNamespace(
            mux_queues={("svc-mux", "mux"): object()},
            endpoints={
                ("app", "api"): {
                    "subsets": [
                        {
                            "addresses": [
                                {
                                    "ip": "10.0.0.1",
                                    "targetRef": {
                                        "kind": "Pod",
                                        "namespace": "app",
                                        "name": "api-0",
                                    },
                                }
                            ],
                        }
                    ]
                }
            },
        )
        channel = {
            "metadata": {
                "namespace": "app",
                "name": "api",
                "annotations": {
                    "svc-mux.nowake.ai/ports": "http:80->30080",
                },
            },
            "spec": {
                "loadBalancerClass": "svc-mux.nowake.ai/mux.svc-mux",
                "ports": [
                    {
                        "name": "http",
                        "port": 80,
                        "nodePort": 30080,
                        "protocol": "TCP",
                    }
                ],
            },
            "status": {},
        }
        mux_channels = {("svc-mux", "mux"): {FrozenDict(channel)}}
        mux_service = {
            "metadata": {
                "annotations": {
                    "external-dns.alpha.kubernetes.io/hostname": "mux.example.com",
                }
            },
            "status": {"loadBalancer": {"ingress": [{"ip": "203.0.113.10"}]}},
        }

        store.update_mux_state(memo, mux_channels, ("svc-mux", "mux"), mux_service)

        snapshot = store.snapshot()
        self.assertIn("svc-mux/mux", snapshot["mux_services"])
        self.assertEqual(
            snapshot["channel_services"]["app/api"]["external_dns"],
            "mux.example.com",
        )
        self.assertEqual(snapshot["endpoints"]["app/api"]["pods"], ["app/api-0"])
        self.assertFalse(store.topology()["svc-mux/mux"]["mux_missing"])

    def test_delete_mux_state_removes_mux_channels_and_endpoints(self):
        store = DebugStateStore()
        memo = SimpleNamespace(
            mux_queues={("svc-mux", "mux"): object()},
            endpoints={},
        )
        channel = {
            "metadata": {"namespace": "app", "name": "api", "annotations": {}},
            "spec": {
                "loadBalancerClass": "svc-mux.nowake.ai/mux.svc-mux",
                "ports": [],
            },
            "status": {},
        }

        store.update_mux_state(
            memo,
            {("svc-mux", "mux"): {FrozenDict(channel)}},
            ("svc-mux", "mux"),
            {},
        )
        store.delete_mux_state(("svc-mux", "mux"))

        snapshot = store.snapshot()
        self.assertEqual(snapshot["mux_services"], {})
        self.assertEqual(snapshot["channel_services"], {})


class FrozenDict(dict):
    def __hash__(self):
        return id(self)


if __name__ == "__main__":
    unittest.main()
