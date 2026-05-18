import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from refs import get_mux_from_lb_class


class MuxReferenceTest(unittest.TestCase):
    def test_parse_name_with_default_namespace(self):
        self.assertEqual(
            get_mux_from_lb_class(
                "svc-mux.nowake.ai/mux",
                api_prefix="svc-mux.nowake.ai",
                default_mux_namespace="svc-mux",
            ),
            ("svc-mux", "mux"),
        )

    def test_parse_name_and_namespace(self):
        self.assertEqual(
            get_mux_from_lb_class(
                "svc-mux.nowake.ai/payments.edge",
                api_prefix="svc-mux.nowake.ai",
                default_mux_namespace="svc-mux",
            ),
            ("edge", "payments"),
        )

    def test_reject_invalid_prefix(self):
        with self.assertRaises(ValueError):
            get_mux_from_lb_class(
                "example.com/mux",
                api_prefix="svc-mux.nowake.ai",
                default_mux_namespace="svc-mux",
            )

    def test_reject_invalid_dns_label(self):
        with self.assertRaises(ValueError):
            get_mux_from_lb_class(
                "svc-mux.nowake.ai/Bad_Name",
                api_prefix="svc-mux.nowake.ai",
                default_mux_namespace="svc-mux",
            )


if __name__ == "__main__":
    unittest.main()
