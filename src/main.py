"""Executable entrypoint for Service LoadBalancer Multiplexer."""

import logging
import signal
import sys

import kopf

import controller  # noqa: F401 - importing registers Kopf handlers


def run():
    logging.basicConfig(
        level=logging.DEBUG,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        stream=sys.stdout,
    )

    shutdown_state = {"requested": False}
    original_sigint_handler = signal.getsignal(signal.SIGINT)

    def graceful_exit_handler(signum, frame):  # noqa: ARG001
        if not shutdown_state["requested"]:
            logging.info("\nReceived interrupt signal, initiating graceful shutdown...")
            shutdown_state["requested"] = True
            signal.signal(signal.SIGINT, original_sigint_handler)
        else:
            logging.warning("Force shutdown requested")
            sys.exit(1)

    signal.signal(signal.SIGINT, graceful_exit_handler)
    logging.info("Press Ctrl+C to shutdown gracefully (twice to force)")

    kopf.run(liveness_endpoint="http://0.0.0.0:8888/healthz", clusterwide=True)


if __name__ == "__main__":
    run()
