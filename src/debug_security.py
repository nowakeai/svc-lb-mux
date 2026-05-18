"""Security middleware for the debug API and UI."""

import logging
import time

from fastapi import Request

logger = logging.getLogger(__name__)

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=()",
}


async def security_middleware(request: Request, call_next):
    """Add baseline security headers and structured request logs."""
    start = time.monotonic()
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)

    duration_ms = (time.monotonic() - start) * 1000
    logger.info(
        "debug request method=%s path=%s status=%s duration_ms=%.2f",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response
