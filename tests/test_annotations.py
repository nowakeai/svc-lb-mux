import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from annotations import (
    format_channel_port_annotation,
    format_summary_annotation,
    format_topology_annotation,
    parse_port_mappings,
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

    def test_parse_port_mappings(self):
        self.assertEqual(
            parse_port_mappings("http:8080->30000, grpc:9090->31000"),
            {"http": "30000", "grpc": "31000"},
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

    def test_topology_annotation_ignores_channel_external_dns_hostname(self):
        memo = SimpleNamespace(endpoints={})
        channel = {
            "metadata": {
                "namespace": "app",
                "name": "api",
                "annotations": {
                    "external-dns.alpha.kubernetes.io/hostname": "ignored.example.com"
                },
            },
            "spec": {"ports": [{"name": "http", "port": 80, "protocol": "TCP"}]},
        }

        topology = format_topology_annotation([channel], memo, "mux.example.com")

        self.assertIn("DNS: mux.example.com", topology)
        self.assertNotIn("ignored.example.com", topology)
        self.assertNotIn("(custom)", topology)

    def test_topology_annotation_uses_mux_port_annotation_without_nodeport(self):
        memo = SimpleNamespace(endpoints={})
        channel = {
            "metadata": {
                "namespace": "app",
                "name": "api",
                "annotations": {"svc-mux.nowake.ai/ports": "http:8081->30000"},
            },
            "spec": {"ports": [{"name": "http", "port": 8081, "protocol": "TCP"}]},
        }

        topology = format_topology_annotation([channel], memo, "34.83.176.141")

        self.assertIn("Ports: http:8081->30000", topology)


if __name__ == "__main__":
    unittest.main()
