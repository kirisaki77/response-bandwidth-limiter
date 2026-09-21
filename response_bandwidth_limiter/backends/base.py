"""Storage interface, shared result types, and validation helpers."""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


_APPROX_COUNTER_PREFIX = "__rbl_counter__"


@dataclass(frozen=True)
class SlidingWindowResult:
    hit_count: int
    oldest_timestamp: float | None
    current_timestamp: float
    # Timestamp of the hit whose expiry makes room for the next request.
    retry_after_timestamp: float | None = None


class StorageUnavailableError(RuntimeError):
    pass


def _validate_limit(name: str, value: int) -> None:
    if not isinstance(value, int):
        raise TypeError(f"{name} must be an integer.")
    if value <= 0:
        raise ValueError(f"{name} must be greater than 0.")


def _validate_expire(expire: int | None) -> None:
    if expire is None:
        return
    _validate_limit("expire", expire)


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
        *,
        max_hits: int | None = None,
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
