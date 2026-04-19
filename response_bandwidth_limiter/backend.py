import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, Mapping, Sequence, Tuple

from .models import Rule


@dataclass(frozen=True)
class HitResult:
    hit_count: int
    oldest_timestamp: float | None
    current_timestamp: float


class CounterBackendUnavailableError(RuntimeError):
    pass


class CounterBackend(ABC):
    @abstractmethod
    async def record_hit(
        self,
        request_key: str,
        handler_name: str,
        rule_index: int,
        window_seconds: int,
    ) -> HitResult:
        raise NotImplementedError

    async def cleanup_expired(self, active_rules: Mapping[str, Sequence[Rule]]) -> None:
        return None


class InMemoryBackend(CounterBackend):
    def __init__(self, time_provider: Callable[[], float] | None = None, max_counters: int = 10000):
        if max_counters <= 0:
            raise ValueError("max_counters は1以上である必要があります。")
        self._lock = threading.RLock()
        self._time_provider = time_provider or time.monotonic
        self._request_counters: Dict[Tuple[str, str, int], Deque[float]] = {}
        self._max_counters = max_counters

    @property
    def request_counters(self) -> Dict[Tuple[str, str, int], Deque[float]]:
        with self._lock:
            return dict(self._request_counters)

    async def record_hit(
        self,
        request_key: str,
        handler_name: str,
        rule_index: int,
        window_seconds: int,
    ) -> HitResult:
        with self._lock:
            now = self._time_provider()
            counter_key = (request_key, handler_name, rule_index)
            history = self._request_counters.get(counter_key)
            if history is None:
                self._evict_if_needed()
                history = deque()
                self._request_counters[counter_key] = history

            self._cleanup_counter(history, now, window_seconds)
            history.append(now)

            return HitResult(
                hit_count=len(history),
                oldest_timestamp=history[0] if history else None,
                current_timestamp=now,
            )

    def _evict_if_needed(self) -> None:
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

    def _cleanup_counter(self, history: Deque[float], now: float, window_seconds: int) -> None:
        threshold = now - window_seconds
        while history and history[0] <= threshold:
            history.popleft()

    async def cleanup_expired(self, active_rules: Mapping[str, Sequence[Rule]]) -> None:
        with self._lock:
            now = self._time_provider()
            stale_keys: list[Tuple[str, str, int]] = []

            for counter_key, history in self._request_counters.items():
                _, handler_name, rule_index = counter_key
                rules = active_rules.get(handler_name)
                if rules is None or rule_index >= len(rules):
                    stale_keys.append(counter_key)
                    continue

                self._cleanup_counter(history, now, rules[rule_index].window_seconds)
                if not history:
                    stale_keys.append(counter_key)

            for counter_key in stale_keys:
                self._request_counters.pop(counter_key, None)
