"""Debug webserver runtime for Service LoadBalancer Multiplexer."""

import asyncio
import logging
import threading

import uvicorn

from config import DRYRUN_MODE
from debug_auth import AUTH_ENABLED
from debug_routes import create_app
from debug_state import delete_mux_state, record_event, update_mux_state

logger = logging.getLogger(__name__)


async def _serve_until_shutdown(port: int, shutdown_event: threading.Event):
    if DRYRUN_MODE:
        logger.info("Running in DRYRUN mode - operator will be read-only")

    if AUTH_ENABLED:
        logger.info("Authentication enabled for debug webserver")

    config = uvicorn.Config(
        create_app(),
        host="0.0.0.0",
        port=port,
        access_log=False,
        log_config=None,
    )
    server = uvicorn.Server(config)

    async def _monitor_shutdown():
        while not shutdown_event.is_set():
            await asyncio.sleep(0.1)
        server.should_exit = True

    monitor_task = asyncio.create_task(_monitor_shutdown())
    try:
        await server.serve()
    finally:
        monitor_task.cancel()
        await asyncio.gather(monitor_task, return_exceptions=True)


def _run_webserver_in_thread(port: int, shutdown_event: threading.Event):
    """Run the FastAPI webserver in a separate thread."""
    try:
        asyncio.run(_serve_until_shutdown(port, shutdown_event))
    except Exception as error:
        logger.error("Webserver thread error: %s", error, exc_info=True)


def start_webserver_thread(port: int = 8080):
    """Start the debug webserver in a separate daemon thread."""
    shutdown_event = threading.Event()
    thread = threading.Thread(
        target=_run_webserver_in_thread,
        args=(port, shutdown_event),
        daemon=True,
        name="WebserverThread",
    )
    thread.start()
    logger.info("Webserver thread started (daemon)")
    return thread, shutdown_event


async def start_webserver_async(port: int = 8080, memo=None):
    """Start the debug webserver as an asyncio task."""
    shutdown_event = threading.Event()
    task = asyncio.create_task(_serve_until_shutdown(port, shutdown_event))
    return task, shutdown_event


__all__ = [
    "create_app",
    "start_webserver_thread",
    "start_webserver_async",
    "record_event",
    "update_mux_state",
    "delete_mux_state",
    "DRYRUN_MODE",
]
