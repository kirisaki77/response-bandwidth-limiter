"""Thread-safe storage within a single process."""

import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, Mapping, Sequence, Tuple

from .base import SlidingWindowResult, Storage, StorageUnavailableError, _validate_expire, _validate_limit


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
        *,
        max_hits: int | None = None,
    ) -> SlidingWindowResult:
        if max_hits is not None:
            _validate_limit("max_hits", max_hits)

        with self._lock:
            now = self._time_provider()
            counter_key = (request_key, handler_name, rule_index)
            history = self._request_counters.get(counter_key)
            if history is None:
                self._evict_counters_if_needed()
                history = deque()
                self._request_counters[counter_key] = history

            self._cleanup_counter(history, now, window_seconds)
            if max_hits is None or len(history) < max_hits:
                history.append(now)
            return SlidingWindowResult(
                hit_count=len(history),
                oldest_timestamp=history[0] if history else None,
                current_timestamp=now,
                retry_after_timestamp=(
                    history[len(history) - max_hits + 1]
                    if max_hits is not None and max_hits > 1 and len(history) >= max_hits
                    else None
                ),
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
        oldest_keys = sorted(
            (candidate for candidate in self._last_access if not candidate.startswith("ip:")),
            key=self._last_access.get,
        )
        if not oldest_keys:
            raise StorageUnavailableError("In-memory storage is full of IP control entries.")
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
