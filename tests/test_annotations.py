import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from annotations import (
    format_channel_port_annotation,
    format_summary_annotation,
    format_topology_annotation,
)


class AnnotationFormattingTest(unittest.TestCase):
    def test_channel_port_annotation(self):
        self.assertEqual(
            format_channel_port_annotation([("http", 80, 30080), ("grpc", 9090, 30090)]),
            "http:80->30080, grpc:9090->30090",
        )

    def test_summary_uses_compact_dns_display(self):
        self.assertEqual(
            format_summary_annotation(
                [{"metadata": {"namespace": "app", "name": "api"}}],
                2,
                3,
                "api.example.com,api-alt.example.com",
            ),
            "1 channel(s) | 2 port(s) | 3 pod(s) | DNS: api.example.com (+1 more)",
        )

    def test_topology_annotation_includes_channels_ports_and_backends(self):
        memo = SimpleNamespace(
            endpoints={
                ("app", "api"): {
                    "subsets": [
                        {
                            "addresses": [{"ip": "10.0.0.1"}, {"ip": "10.0.0.2"}],
                        }
                    ]
                }
            }
        )
        channel = {
            "metadata": {"namespace": "app", "name": "api", "annotations": {}},
            "spec": {
                "ports": [
                    {
                        "name": "http",
                        "port": 80,
                        "nodePort": 30080,
                        "protocol": "TCP",
                    }
                ]
            },
        }

        topology = format_topology_annotation([channel], memo, "mux.example.com")

        self.assertIn("Mux DNS: mux.example.com", topology)
        self.assertIn("  - app/api", topology)
        self.assertIn("Ports: http:80->30080", topology)
        self.assertIn("Backend: 2 pod(s) ready", topology)


if __name__ == "__main__":
    unittest.main()
