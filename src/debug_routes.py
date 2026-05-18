"""FastAPI routes for the debug API and UI."""

import asyncio
import socket
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from config import DEBUG_WEB_ACTIONS_ENABLED
from debug_auth import auth_middleware
from debug_security import security_middleware
from debug_state import record_event, state_store


def create_app() -> FastAPI:
    """Create the FastAPI debug web application."""
    app = FastAPI(
        title="Service LoadBalancer Multiplexer Debug UI",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.middleware("http")(auth_middleware)
    app.middleware("http")(security_middleware)

    @app.get("/")
    async def handle_index():
        html_path = Path(__file__).parent / "index.html"
        return FileResponse(html_path)

    @app.get("/api/state")
    async def handle_state():
        return JSONResponse(state_store.snapshot())

    @app.get("/api/topology")
    async def handle_topology():
        return JSONResponse(state_store.topology())

    @app.get("/api/config")
    async def handle_config():
        return JSONResponse({"actions_enabled": DEBUG_WEB_ACTIONS_ENABLED})

    @app.get("/api/test-tcp")
    async def handle_test_tcp(
        host: str | None = None,
        port: str | None = None,
        resource: str = "unknown",
    ):
        if not DEBUG_WEB_ACTIONS_ENABLED:
            return JSONResponse(
                {"success": False, "error": "Debug actions are disabled"},
                status_code=403,
            )

        if not host or not port:
            return JSONResponse(
                {"success": False, "error": "Missing host or port"},
                status_code=400,
            )

        try:
            port_int = int(port)
        except ValueError:
            return JSONResponse(
                {"success": False, "error": "Invalid port"},
                status_code=400,
            )
        if not 1 <= port_int <= 65535:
            return JSONResponse(
                {"success": False, "error": "Port out of range"},
                status_code=400,
            )

        return await _handle_tcp_result(host, port_int, resource)

    @app.get("/healthz")
    async def handle_healthz():
        return JSONResponse({"status": "ok"})

    return app


async def _handle_tcp_result(host: str, port: int, resource: str):
    try:
        result = await asyncio.to_thread(test_tcp_connection, host, port)
        if result == 0:
            record_event(
                "Normal",
                resource,
                f"ConnectionTest: Successfully connected to {host}:{port}",
            )
            return JSONResponse(
                {"success": True, "message": f"Connected to {host}:{port}"}
            )

        record_event(
            "Warning",
            resource,
            f"ConnectionTest: Connection failed to {host}:{port} (error code {result})",
        )
        return JSONResponse(
            {
                "success": False,
                "error": f"Connection failed with error code {result}",
            }
        )
    except socket.gaierror as error:
        record_event(
            "Warning",
            resource,
            f"ConnectionTest: DNS resolution failed for {host}:{port} - {error}",
        )
        return JSONResponse(
            {"success": False, "error": f"DNS resolution failed: {error}"}
        )
    except socket.timeout:
        record_event(
            "Warning",
            resource,
            f"ConnectionTest: Connection timeout to {host}:{port}",
        )
        return JSONResponse({"success": False, "error": "Connection timeout"})
    except Exception as error:
        record_event(
            "Error",
            resource,
            f"ConnectionTest: Unexpected error testing {host}:{port} - {error}",
        )
        return JSONResponse(
            {"success": False, "error": f"Unexpected error: {error}"}
        )


def test_tcp_connection(host: str, port: int) -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(3.0)
        return sock.connect_ex((host, port))
    finally:
        sock.close()
