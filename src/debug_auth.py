"""Authentication helpers for the debug API."""

import asyncio
import base64
import hmac
import os

from fastapi import Request
from fastapi.responses import Response

AUTH_TOKEN = os.environ.get("DEBUG_WEB_AUTH_TOKEN", os.environ.get("AUTH_TOKEN", ""))
AUTH_ENABLED = bool(AUTH_TOKEN)


def check_auth(request: Request):
    """Check HTTP Basic authentication against the configured debug token."""
    if not AUTH_ENABLED:
        return True

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Basic "):
        try:
            credentials = base64.b64decode(auth_header[6:]).decode("utf-8")
            if ":" in credentials:
                _, password = credentials.split(":", 1)
                if hmac.compare_digest(password, AUTH_TOKEN):
                    return True
        except Exception:
            pass

    return False


async def auth_middleware(request: Request, call_next):
    """Check authentication for all requests except the health check."""
    if request.url.path == "/healthz":
        return await call_next(request)

    if not check_auth(request):
        await asyncio.sleep(2)
        return Response(
            content="Unauthorized - authentication required",
            status_code=401,
            headers={
                "WWW-Authenticate": (
                    'Basic realm="Service LoadBalancer Multiplexer Debug UI"'
                )
            },
        )

    return await call_next(request)
