import contextlib
import importlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config import ANNOTATION_EXTERNAL_PORTS, ANNOTATION_PORT_RANGE

_controller = None


class BuildMuxStateTest(unittest.TestCase):
    def test_store_load_failure_falls_back_for_static_ports(self):
        mux = mux_service(annotations={ANNOTATION_PORT_RANGE: "30000-30000"})
        channel = channel_service(port=30001)

        mux_state, state_store = build_state_with_failing_store(mux, [channel])

        self.assertIsNone(state_store)
        self.assertFalse(mux_state.changed)

        mux_state.record_channel_claims(channel, "http:30001->30001")
        saved = mux_state.to_state()

        self.assertTrue(mux_state.changed)
        self.assertEqual(saved["portClaims"][0]["muxPort"], 30001)
        self.assertEqual(saved["portClaims"][0]["source"], "static")

    def test_store_load_failure_disables_auto_allocation_even_with_port_range(self):
        mux = mux_service(annotations={ANNOTATION_PORT_RANGE: "30000-30000"})
        channel = channel_service(
            port=80,
            annotations={ANNOTATION_EXTERNAL_PORTS: "http:auto"},
        )

        mux_state, state_store = build_state_with_failing_store(mux, [channel])

        self.assertIsNone(state_store)
        self.assertEqual(mux_state.ranges, [])
        with self.assertRaisesRegex(ValueError, "requires a mux port range"):
            mux_state.allocate(channel, channel["spec"]["ports"][0])


def build_state_with_failing_store(mux, channels):
    controller = controller_module()
    store = Mock()
    store.load.side_effect = ValueError("bad mux state")
    with (
        patch.object(controller, "ConfigMapAllocationStore", return_value=store),
        patch.object(controller.events, "error") as emit_error,
    ):
        mux_state, state_store = controller.build_mux_state(
            mux,
            (mux.namespace, mux.name),
            channels,
            {"metadata": {"namespace": mux.namespace, "name": mux.name}},
        )

    store.load.assert_called_once_with()
    emit_error.assert_called_once()
    return mux_state, state_store


def controller_module():
    global _controller
    if _controller is None:
        with contextlib.redirect_stdout(io.StringIO()):
            _controller = importlib.import_module("controller")
    return _controller


def mux_service(name="mux", namespace="svc-mux", annotations=None):
    class Mux:
        pass

    mux = Mux()
    mux.name = name
    mux.namespace = namespace
    mux.annotations = annotations or {}
    return mux


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
