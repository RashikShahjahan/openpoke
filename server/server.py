#!/usr/bin/env python3
"""CLI entrypoint for running OpenPoke as a messaging-only daemon."""

from __future__ import annotations

import argparse
import asyncio
import signal

from .config import get_settings
from .logging_config import configure_logging, logger
from .messaging.gateway import get_messaging_gateway
from .services import get_trigger_scheduler


async def run_daemon() -> None:
    """Run background services until interrupted."""

    scheduler = get_trigger_scheduler()
    gateway = get_messaging_gateway()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_shutdown() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, request_shutdown)

    await scheduler.start()
    await gateway.start()
    logger.info("OpenPoke messaging daemon started")

    try:
        await stop_event.wait()
    finally:
        logger.info("OpenPoke messaging daemon stopping")
        await gateway.stop()
        await scheduler.stop()


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="OpenPoke messaging daemon")
    parser.add_argument(
        "--require-signal",
        action="store_true",
        help="Exit if Signal support is not enabled in configuration",
    )
    args = parser.parse_args()

    configure_logging()
    if args.require_signal and not settings.signal_enabled:
        raise SystemExit("Signal support is disabled. Set OPENPOKE_SIGNAL_ENABLED=1.")

    asyncio.run(run_daemon())


if __name__ == "__main__":  # pragma: no cover - CLI invocation guard
    main()
