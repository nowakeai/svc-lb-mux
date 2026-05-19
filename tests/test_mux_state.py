import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from models import MuxPort
from mux_state import MuxState
from reconcile import find_mux_port_conflicts, port_hash


class MuxStateTest(unittest.TestCase):
    def test_static_claim_is_persisted(self):
        channel = channel_service(name="api", port=20301)
        state = MuxState(("svc-mux", "mux"), channels=[channel])

        state.record_channel_claims(channel, "http:20301->20301")
        saved = state.to_state()

        self.assertEqual(saved["portClaims"][0]["source"], "static")
        self.assertEqual(saved["portClaims"][0]["muxPort"], 20301)
        self.assertEqual(saved["allocations"], [])

    def test_existing_static_claim_preserves_owner_with_placeholder_mux(self):
        existing = channel_service(name="z-existing", port=80)
        newcomer = channel_service(name="a-new", port=80)
        existing_hash = port_hash("app", "z-existing", "http")
        state = MuxState(
            ("svc-mux", "mux"),
            state={
                "portClaims": [
                    {
                        "namespace": "app",
                        "service": "z-existing",
                        "portName": "http",
                        "protocol": "TCP",
                        "channelPort": 80,
                        "muxPort": 80,
                        "port": 80,
                        "source": "static",
                    }
                ]
            },
            channels=[newcomer, existing],
        )

        self.assertEqual(state.port_owners(), {(80, "TCP"): f"app/z-existing:{existing_hash}"})
        conflicts = find_mux_port_conflicts(
            state.port_owners(),
            newcomer,
            {MuxPort(port_hash("app", "a-new", "http"), 80, "TCP")},
        )

        self.assertEqual(len(conflicts), 1)
        self.assertIn("app/z-existing", conflicts[0])

    def test_old_auto_allocations_are_migrated_to_claims(self):
        channel = channel_service(
            name="api",
            port=80,
            annotations={"svc-mux.nowake.ai/external-ports": "http:auto"},
        )
        state = MuxState(
            ("svc-mux", "mux"),
            ranges=[(30000, 30000)],
            state={
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
            },
            channels=[channel],
        )

        self.assertTrue(state.changed)
        self.assertEqual(state.allocate(channel, channel["spec"]["ports"][0]), 30000)
        state.record_channel_claims(channel, "http:80->30000")
        saved = state.to_state()

        self.assertEqual(saved["portClaims"][0]["muxPort"], 30000)
        self.assertEqual(saved["allocations"][0]["port"], 30000)

    def test_deleted_channel_claim_is_pruned(self):
        state = MuxState(
            ("svc-mux", "mux"),
            state={
                "portClaims": [
                    {
                        "namespace": "app",
                        "service": "deleted",
                        "portName": "http",
                        "protocol": "TCP",
                        "channelPort": 80,
                        "muxPort": 80,
                        "port": 80,
                        "source": "static",
                    }
                ]
            },
            channels=[],
        )

        self.assertTrue(state.changed)
        self.assertEqual(state.to_state()["portClaims"], [])

    def test_explicit_external_port_claim_is_persisted_as_static(self):
        channel = channel_service(
            name="api",
            port=80,
            annotations={"svc-mux.nowake.ai/external-ports": "http:30080"},
        )
        state = MuxState(("svc-mux", "mux"), channels=[channel])

        state.record_channel_claims(channel, "http:80->30080")
        saved = state.to_state()["portClaims"][0]

        self.assertEqual(saved["source"], "static")
        self.assertEqual(saved["channelPort"], 80)
        self.assertEqual(saved["muxPort"], 30080)
        self.assertEqual(state.port_owners(), {(30080, "TCP"): f"app/api:{port_hash('app', 'api', 'http')}"})

    def test_changed_static_port_updates_existing_claim(self):
        channel = channel_service(name="api", port=81)
        state = MuxState(
            ("svc-mux", "mux"),
            state={
                "portClaims": [
                    {
                        "namespace": "app",
                        "service": "api",
                        "portName": "http",
                        "protocol": "TCP",
                        "channelPort": 80,
                        "muxPort": 80,
                        "port": 80,
                        "source": "static",
                    }
                ]
            },
            channels=[channel],
        )

        state.record_channel_claims(channel, "http:81->81")
        saved = state.to_state()["portClaims"][0]

        self.assertTrue(state.changed)
        self.assertEqual(saved["channelPort"], 81)
        self.assertEqual(saved["muxPort"], 81)
        self.assertEqual(state.port_owners(), {(81, "TCP"): f"app/api:{port_hash('app', 'api', 'http')}"})

    def test_auto_claim_outside_range_is_reallocated(self):
        channel = channel_service(
            name="api",
            port=80,
            annotations={"svc-mux.nowake.ai/external-ports": "http:auto"},
        )
        state = MuxState(
            ("svc-mux", "mux"),
            ranges=[(30000, 30000)],
            state={
                "portClaims": [
                    {
                        "namespace": "app",
                        "service": "api",
                        "portName": "http",
                        "protocol": "TCP",
                        "channelPort": 80,
                        "muxPort": 32000,
                        "port": 32000,
                        "source": "auto",
                    }
                ]
            },
            channels=[channel],
        )

        self.assertEqual(state.allocate(channel, channel["spec"]["ports"][0]), 30000)
        state.record_channel_claims(channel, "http:80->30000")

        self.assertTrue(state.changed)
        self.assertEqual(state.to_state()["portClaims"][0]["muxPort"], 30000)

    def test_auto_allocation_reports_exhaustion(self):
        channel = channel_service(
            name="api",
            port=80,
            annotations={"svc-mux.nowake.ai/external-ports": "http:auto"},
        )
        state = MuxState(
            ("svc-mux", "mux"),
            ranges=[(30000, 30000)],
            channels=[channel],
            reserved_ports={(30000, "TCP")},
        )

        with self.assertRaisesRegex(ValueError, "No available TCP port"):
            state.allocate(channel, channel["spec"]["ports"][0])

    def test_auto_allocation_avoids_static_claims(self):
        static = channel_service(name="static", port=30000)
        auto = channel_service(
            name="auto",
            port=80,
            annotations={"svc-mux.nowake.ai/external-ports": "http:auto"},
        )
        state = MuxState(
            ("svc-mux", "mux"),
            ranges=[(30000, 30001)],
            channels=[static, auto],
            reserved_ports={(30000, "TCP")},
        )

        self.assertEqual(state.allocate(auto, auto["spec"]["ports"][0]), 30001)


def channel_service(name="api", namespace="app", port=80, annotations=None):
    return {
        "metadata": {
            "namespace": namespace,
            "name": name,
            "annotations": annotations or {},
        },
        "spec": {
            "ports": [
                {
                    "name": "http",
                    "port": port,
                    "targetPort": "http",
                    "protocol": "TCP",
                }
            ]
        },
    }


if __name__ == "__main__":
    unittest.main()
