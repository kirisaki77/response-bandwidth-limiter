import logging
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from multiprocessing.managers import SyncManager
from typing import Any, Callable, Deque, Dict, Mapping, MutableMapping, Sequence, Tuple


logger = logging.getLogger(__name__)

_APPROX_COUNTER_PREFIX = "__rbl_counter__"
_EXPIRY_PREFIX = "__rbl_exp__:"


@dataclass(frozen=True)
class SlidingWindowResult:
    hit_count: int
    oldest_timestamp: float | None
    current_timestamp: float


class StorageUnavailableError(RuntimeError):
    pass


def _validate_limit(name: str, value: int) -> None:
    if not isinstance(value, int):
        raise TypeError(f"{name} は整数である必要があります。")
    if value <= 0:
        raise ValueError(f"{name} は1以上である必要があります。")


def _validate_expire(expire: int | None) -> None:
    if expire is None:
        return
    _validate_limit("expire", expire)


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


class Storage(ABC):
    @abstractmethod
    async def get(self, key: str) -> Any | None:
        raise NotImplementedError

    @abstractmethod
    async def set(self, key: str, value: Any, expire: int | None = None) -> None:
        raise NotImplementedError

    @abstractmethod
    async def incr(self, key: str, expire: int | None = None) -> int:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, key: str) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        return None

    async def record_hit(
        self,
        request_key: str,
        handler_name: str,
        rule_index: int,
        window_seconds: int,
    ) -> SlidingWindowResult:
        now = time.time()
        bucket = int(now // window_seconds)
        bucket_start = float(bucket * window_seconds)
        counter_key = self._build_approx_counter_key(request_key, handler_name, rule_index, bucket)
        hit_count = await self.incr(counter_key, expire=max(1, window_seconds * 2))
        return SlidingWindowResult(
            hit_count=hit_count,
            oldest_timestamp=bucket_start,
            current_timestamp=now,
        )

    def cleanup_handler_counters(self, handler_name: str) -> None:
        return None

    def cleanup_orphaned_counters(self, active_rules: Mapping[str, Sequence[Any]]) -> None:
        return None

    def _build_approx_counter_key(self, request_key: str, handler_name: str, rule_index: int, bucket: int) -> str:
        return f"{_APPROX_COUNTER_PREFIX}:{handler_name}:{rule_index}:{request_key}:{bucket}"

    def _build_approx_handler_prefix(self, handler_name: str) -> str:
        return f"{_APPROX_COUNTER_PREFIX}:{handler_name}:"


class InMemoryStorage(Storage):
    """
    Thread-safe process-local storage.

    This storage is shared across threads in the same worker only. It does not
    provide consistency across multiple workers or processes.
    """

    def __init__(
        self,
        time_provider: Callable[[], float] | None = None,
        *,
        max_keys: int = 10000,
        max_counters: int = 10000,
    ):
        _validate_limit("max_keys", max_keys)
        _validate_limit("max_counters", max_counters)
        self._lock = threading.RLock()
        self._time_provider = time_provider or time.monotonic
        self._values: Dict[str, Any] = {}
        self._expires: Dict[str, float] = {}
        self._last_access: Dict[str, float] = {}
        self._request_counters: Dict[Tuple[str, str, int], Deque[float]] = {}
        self._max_keys = max_keys
        self._max_counters = max_counters
        self._closed = False

    @property
    def request_counters(self) -> Dict[Tuple[str, str, int], Deque[float]]:
        with self._lock:
            return dict(self._request_counters)

    async def get(self, key: str) -> Any | None:
        with self._lock:
            now = self._time_provider()
            if self._delete_if_expired(key, now):
                return None
            value = self._values.get(key)
            if key in self._values:
                self._touch_key(key, now)
            return value

    async def set(self, key: str, value: Any, expire: int | None = None) -> None:
        _validate_expire(expire)
        with self._lock:
            now = self._time_provider()
            self._evict_keys_if_needed(key, now)
            self._values[key] = value
            self._touch_key(key, now)
            self._set_expiry(key, now, expire)

    async def incr(self, key: str, expire: int | None = None) -> int:
        _validate_expire(expire)
        with self._lock:
            now = self._time_provider()
            self._delete_if_expired(key, now)
            self._evict_keys_if_needed(key, now)
            current = int(self._values.get(key, 0)) + 1
            self._values[key] = current
            self._touch_key(key, now)
            if expire is not None:
                self._set_expiry(key, now, expire)
            return current

    async def delete(self, key: str) -> None:
        with self._lock:
            self._delete_key(key)

    async def close(self) -> None:
        with self._lock:
            self._closed = True

    async def record_hit(
        self,
        request_key: str,
        handler_name: str,
        rule_index: int,
        window_seconds: int,
    ) -> SlidingWindowResult:
        with self._lock:
            now = self._time_provider()
            counter_key = (request_key, handler_name, rule_index)
            history = self._request_counters.get(counter_key)
            if history is None:
                self._evict_counters_if_needed()
                history = deque()
                self._request_counters[counter_key] = history

            self._cleanup_counter(history, now, window_seconds)
            history.append(now)
            return SlidingWindowResult(
                hit_count=len(history),
                oldest_timestamp=history[0] if history else None,
                current_timestamp=now,
            )

    def cleanup_handler_counters(self, handler_name: str) -> None:
        with self._lock:
            stale_keys = [key for key in self._request_counters if key[1] == handler_name]
            for counter_key in stale_keys:
                self._request_counters.pop(counter_key, None)

            approx_prefix = self._build_approx_handler_prefix(handler_name)
            approx_keys = [key for key in self._values if key.startswith(approx_prefix)]
            for approx_key in approx_keys:
                self._delete_key(approx_key)

    def cleanup_orphaned_counters(self, active_rules: Mapping[str, Sequence[Any]]) -> None:
        with self._lock:
            now = self._time_provider()
            stale_keys: list[Tuple[str, str, int]] = []

            for counter_key, history in self._request_counters.items():
                _, handler_name, rule_index = counter_key
                rules = active_rules.get(handler_name)
                if rules is None or rule_index >= len(rules):
                    stale_keys.append(counter_key)
                    continue

                window_seconds = getattr(rules[rule_index], "window_seconds", None)
                if window_seconds is None:
                    stale_keys.append(counter_key)
                    continue

                self._cleanup_counter(history, now, int(window_seconds))
                if not history:
                    stale_keys.append(counter_key)

            for counter_key in stale_keys:
                self._request_counters.pop(counter_key, None)

    def _cleanup_counter(self, history: Deque[float], now: float, window_seconds: int) -> None:
        threshold = now - window_seconds
        while history and history[0] <= threshold:
            history.popleft()

    def _evict_counters_if_needed(self) -> None:
        if len(self._request_counters) < self._max_counters:
            return

        empty_keys = [key for key, history in self._request_counters.items() if not history]
        for key in empty_keys:
            self._request_counters.pop(key, None)

        if len(self._request_counters) < self._max_counters:
            return

        trim_by = max(1, self._max_counters // 10)
        target_size = max(0, self._max_counters - trim_by)
        overflow = len(self._request_counters) - target_size
        oldest_keys = sorted(
            self._request_counters,
            key=lambda key: self._request_counters[key][-1] if self._request_counters[key] else float("-inf"),
        )
        for key in oldest_keys[:overflow]:
            self._request_counters.pop(key, None)

    def _evict_keys_if_needed(self, key: str, now: float) -> None:
        if key in self._values or len(self._values) < self._max_keys:
            return

        expired_keys = [candidate for candidate in list(self._values) if self._delete_if_expired(candidate, now)]
        if expired_keys:
            return

        trim_by = max(1, self._max_keys // 10)
        target_size = max(0, self._max_keys - trim_by)
        overflow = len(self._values) - target_size
        oldest_keys = sorted(self._last_access, key=self._last_access.get)
        for candidate in oldest_keys[:overflow]:
            self._delete_key(candidate)

    def _set_expiry(self, key: str, now: float, expire: int | None) -> None:
        if expire is None:
            self._expires.pop(key, None)
            return
        self._expires[key] = now + expire

    def _delete_if_expired(self, key: str, now: float) -> bool:
        expires_at = self._expires.get(key)
        if expires_at is None or expires_at > now:
            return False
        self._delete_key(key)
        return True

    def _delete_key(self, key: str) -> None:
        self._values.pop(key, None)
        self._expires.pop(key, None)
        self._last_access.pop(key, None)

    def _touch_key(self, key: str, now: float) -> None:
        self._last_access[key] = now


class ManagerStorage(Storage):
    """
    Experimental shared storage using multiprocessing.Manager proxies.

    This implementation is slower than dedicated external storage, is not
    suitable for high-load environments, and does not guarantee consistency.
    Exact sliding-window semantics are not supported; the default approximate
    record_hit implementation is used instead.
    """

    _experimental = True

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

    @classmethod
    def from_manager(
        cls,
        manager: SyncManager,
        *,
        time_provider: Callable[[], float] | None = None,
    ) -> "ManagerStorage":
        return cls(manager.dict(), manager.Lock(), time_provider=time_provider)

    async def get(self, key: str) -> Any | None:
        with self._shared_lock:
            now = self._time_provider()
            if self._delete_if_expired(key, now):
                return None
            return self._shared_dict.get(key)

    async def set(self, key: str, value: Any, expire: int | None = None) -> None:
        _validate_expire(expire)
        with self._shared_lock:
            now = self._time_provider()
            self._shared_dict[key] = value
            self._set_expiry(key, now, expire)

    async def incr(self, key: str, expire: int | None = None) -> int:
        _validate_expire(expire)
        with self._shared_lock:
            now = self._time_provider()
            self._delete_if_expired(key, now)
            current = int(self._shared_dict.get(key, 0)) + 1
            self._shared_dict[key] = current
            if expire is not None:
                self._set_expiry(key, now, expire)
            return current

    async def delete(self, key: str) -> None:
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