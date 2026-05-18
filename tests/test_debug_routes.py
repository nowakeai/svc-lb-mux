import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

import debug_routes
from debug_routes import create_app


class DebugRoutesTest(unittest.TestCase):
    def test_healthz(self):
        client = TestClient(create_app())

        response = client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_state_route_returns_debug_state(self):
        client = TestClient(create_app())

        response = client.get("/api/state")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("mux_services", body)
        self.assertIn("channel_services", body)
        self.assertIn("endpoints", body)
        self.assertIn("events", body)

    def test_security_headers_are_applied(self):
        client = TestClient(create_app())

        response = client.get("/api/state")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertEqual(
            response.headers["permissions-policy"],
            "camera=(), geolocation=(), microphone=()",
        )

    def test_config_reports_debug_actions_disabled_by_default(self):
        client = TestClient(create_app())

        response = client.get("/api/config")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"actions_enabled": False})

    def test_test_tcp_is_disabled_by_default(self):
        client = TestClient(create_app())

        response = client.get("/api/test-tcp?host=127.0.0.1&port=80")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "Debug actions are disabled")

    def test_test_tcp_rejects_missing_host_or_port_when_actions_enabled(self):
        previous = debug_routes.DEBUG_WEB_ACTIONS_ENABLED
        debug_routes.DEBUG_WEB_ACTIONS_ENABLED = True
        try:
            client = TestClient(create_app())

            response = client.get("/api/test-tcp")

            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error"], "Missing host or port")
        finally:
            debug_routes.DEBUG_WEB_ACTIONS_ENABLED = previous


if __name__ == "__main__":
    unittest.main()
