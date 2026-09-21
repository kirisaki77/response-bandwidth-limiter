"""Compatibility exports and operational warnings for storage backends."""

import logging

from .backends.base import SlidingWindowResult, Storage, StorageUnavailableError
from .backends.memory import InMemoryStorage
from .backends.manager import ManagerStorage


__all__ = [
    "InMemoryStorage",
    "ManagerStorage",
    "SlidingWindowResult",
    "Storage",
    "StorageUnavailableError",
    "warn_if_storage_requires_caution",
]

logger = logging.getLogger(__name__)


def _detect_multi_worker() -> bool:
    import os

    web_concurrency = os.getenv("WEB_CONCURRENCY")
    if web_concurrency is not None:
        try:
            return int(web_concurrency) > 1
        except ValueError:
            pass

    server_software = (os.getenv("SERVER_SOFTWARE") or "").lower()
    if "gunicorn" in server_software:
        return True

    gunicorn_args = (os.getenv("GUNICORN_CMD_ARGS") or "").strip()
    if gunicorn_args:
        return True

    return False


def warn_if_storage_requires_caution(storage: "Storage") -> None:
    if getattr(storage, "_warning_emitted", False):
        return

    if isinstance(storage, InMemoryStorage) and _detect_multi_worker():
        logger.warning(
            "InMemoryStorage is process-local. Consistency is not guaranteed when worker > 1. "
            "Use RedisStorage for production IP limiting."
        )

    if getattr(storage, "_experimental", False):
        logger.warning(
            "ManagerStorage is experimental. It is slow, not suitable for high-load environments, "
            "and consistency is not guaranteed. Use RedisStorage for production IP limiting."
        )

    setattr(storage, "_warning_emitted", True)
