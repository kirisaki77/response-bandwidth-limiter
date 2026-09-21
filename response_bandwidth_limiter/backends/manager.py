"""Experimental storage backed by multiprocessing manager proxies."""

import asyncio
import time
from multiprocessing.managers import SyncManager
from typing import Any, Callable, Mapping, MutableMapping, Sequence

from .base import Storage, _APPROX_COUNTER_PREFIX, _validate_expire


_EXPIRY_PREFIX = "__rbl_exp__:"


class ManagerStorage(Storage):
    """
    Experimental shared storage using multiprocessing.Manager proxies.

    This implementation is slower than dedicated external storage, is not
    suitable for high-load environments, and does not guarantee consistency.
    Exact sliding-window semantics are not supported; the default approximate
    record_hit implementation is used instead.
    """

    _experimental = True
    _cleanup_interval = 1.0

    def __init__(
        self,
        shared_dict: MutableMapping[str, Any],
        shared_lock: Any,
        *,
        time_provider: Callable[[], float] | None = None,
        owned_manager: SyncManager | None = None,
    ):
        self._shared_dict = shared_dict
        self._shared_lock = shared_lock
        self._time_provider = time_provider or time.monotonic
        self._owned_manager = owned_manager
        self._closed = False
        self._next_cleanup = float("-inf")

    @classmethod
    def from_manager(
        cls,
        manager: SyncManager,
        *,
        time_provider: Callable[[], float] | None = None,
    ) -> "ManagerStorage":
        return cls(manager.dict(), manager.Lock(), time_provider=time_provider)

    async def get(self, key: str) -> Any | None:
        return await asyncio.to_thread(self._get, key)

    def _get(self, key: str) -> Any | None:
        with self._shared_lock:
            now = self._time_provider()
            if self._delete_if_expired(key, now):
                return None
            return self._shared_dict.get(key)

    async def set(self, key: str, value: Any, expire: int | None = None) -> None:
        _validate_expire(expire)
        await asyncio.to_thread(self._set, key, value, expire)

    def _set(self, key: str, value: Any, expire: int | None) -> None:
        with self._shared_lock:
            now = self._time_provider()
            self._cleanup_expired(now)
            self._shared_dict[key] = value
            self._set_expiry(key, now, expire)

    async def incr(self, key: str, expire: int | None = None) -> int:
        _validate_expire(expire)
        return await asyncio.to_thread(self._incr, key, expire)

    def _incr(self, key: str, expire: int | None) -> int:
        with self._shared_lock:
            now = self._time_provider()
            self._cleanup_expired(now)
            self._delete_if_expired(key, now)
            current = int(self._shared_dict.get(key, 0)) + 1
            self._shared_dict[key] = current
            if expire is not None:
                self._set_expiry(key, now, expire)
            return current

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._delete, key)

    def _delete(self, key: str) -> None:
        with self._shared_lock:
            self._delete_key(key)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owned_manager is not None:
            self._owned_manager.shutdown()

    def cleanup_handler_counters(self, handler_name: str) -> None:
        prefix = self._build_approx_handler_prefix(handler_name)
        with self._shared_lock:
            stale_keys = [key for key in list(self._shared_dict.keys()) if str(key).startswith(prefix)]
            for key in stale_keys:
                self._delete_key(str(key))

    def cleanup_orphaned_counters(self, active_rules: Mapping[str, Sequence[Any]]) -> None:
        valid_prefixes = {self._build_approx_handler_prefix(handler_name) for handler_name in active_rules}
        with self._shared_lock:
            stale_keys: list[str] = []
            for key in list(self._shared_dict.keys()):
                key_text = str(key)
                if not key_text.startswith(f"{_APPROX_COUNTER_PREFIX}:"):
                    continue
                if not any(key_text.startswith(prefix) for prefix in valid_prefixes):
                    stale_keys.append(key_text)

            for key in stale_keys:
                self._delete_key(key)

    def _expiry_key(self, key: str) -> str:
        return f"{_EXPIRY_PREFIX}{key}"

    def _cleanup_expired(self, now: float) -> None:
        # Called in a worker thread under the shared lock. One snapshot avoids
        # an IPC round trip per live key, and repeated writes skip the sweep.
        if now < self._next_cleanup:
            return
        for candidate, expires_at in list(self._shared_dict.items()):
            if (
                isinstance(candidate, str)
                and candidate.startswith(_EXPIRY_PREFIX)
                and float(expires_at) <= now
            ):
                self._delete_key(candidate[len(_EXPIRY_PREFIX):])
        self._next_cleanup = now + self._cleanup_interval

    def _set_expiry(self, key: str, now: float, expire: int | None) -> None:
        expiry_key = self._expiry_key(key)
        if expire is None:
            self._shared_dict.pop(expiry_key, None)
            return
        self._shared_dict[expiry_key] = now + expire

    def _delete_if_expired(self, key: str, now: float) -> bool:
        expiry_key = self._expiry_key(key)
        expires_at = self._shared_dict.get(expiry_key)
        if expires_at is None or float(expires_at) > now:
            return False
        self._delete_key(key)
        return True

    def _delete_key(self, key: str) -> None:
        self._shared_dict.pop(key, None)
        self._shared_dict.pop(self._expiry_key(key), None)
