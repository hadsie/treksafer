"""TrekSafer bootstrap.

Load config, setup logging, HTTP caching, and finally start all
configured message transports (e.g. CLI, SignalWire, Email, etc.).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import requests_cache

from .config import get_config, Settings
from .transport import get_transports

def _configure_logging(settings: Settings) -> None:
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    formatter = logging.Formatter(
        '%(asctime)s %(name)s %(levelname)s : %(message)s',
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    logger = logging.getLogger()
    logger.setLevel(settings.log_level)

    fh = logging.FileHandler(f"logs/{settings.env}.log")
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    if settings.env != "prod":
        sh = logging.StreamHandler()
        sh.setFormatter(formatter)
        logger.addHandler(sh)

def _install_caches(settings: Settings) -> None:
    cache_dir = Path("caches")
    cache_dir.mkdir(exist_ok=True)

    bc_api_cache_name = f"cache/bc_fire_api_cache_{settings.env}"
    requests_cache.install_cache(bc_api_cache_name, expire_after=settings.request_cache_timeout)

async def _run_transports(transports: Iterable[BaseTransport]) -> None:
    """Start every transport and keep them alive until explicitly stopped."""
    tasks = [asyncio.create_task(t.listen()) for t in transports]

    try:
        await asyncio.gather(*tasks)
    finally:
        # ask each transport to shut down gracefully
        await asyncio.gather(*(t.stop() for t in transports), return_exceptions=True)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

def run() -> None:
    """Bootstrap TrekSafer and launch all message transport listeners."""
    settings = get_config()
    _configure_logging(settings)
    _install_caches(settings)
    logging.getLogger(__name__).info("TrekSafer starting in %s environment", settings.env)
    print(f"TrekSafer running — environment: {settings.env}")

    try:
        asyncio.run(_run_transports(get_transports(settings)))
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("TrekSafer stopped by user")
