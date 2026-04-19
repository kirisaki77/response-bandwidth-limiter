import hashlib
import time
import uuid
from typing import Any, Literal

from .backend import CounterBackend, CounterBackendUnavailableError, HitResult, InMemoryBackend

try:
    from redis.asyncio import Redis
except ImportError as exc:
    raise ImportError(
        "RedisBackend requires the optional redis dependency. Install it with `pip install response-bandwidth-limiter[redis]`."
    ) from exc


FailureMode = Literal["open", "closed", "local-memory-fallback"]

SLIDING_WINDOW_SCRIPT = """
local current_time = redis.call("TIME")
local now = tonumber(current_time[1]) + (tonumber(current_time[2]) / 1000000)
local window_seconds = tonumber(ARGV[1])
local threshold = now - window_seconds

redis.call("ZREMRANGEBYSCORE", KEYS[1], "-inf", threshold)
redis.call("ZADD", KEYS[1], now, ARGV[2])

local hit_count = redis.call("ZCARD", KEYS[1])
local oldest = redis.call("ZRANGE", KEYS[1], 0, 0, "WITHSCORES")

redis.call("EXPIRE", KEYS[1], math.max(1, math.ceil(window_seconds)))

if oldest[2] then
    return {hit_count, tostring(oldest[2]), tostring(now)}
end

return {hit_count, "", tostring(now)}
"""


class RedisBackend(CounterBackend):
    def __init__(
        self,
        client: Redis,
        *,
        prefix: str = "rbl",
        key_hash: bool = False,
        failure_mode: FailureMode = "open",
        fallback_backend: CounterBackend | None = None,
    ):
        if client is None:
            raise ValueError("client は必須です。")
        if failure_mode not in {"open", "closed", "local-memory-fallback"}:
            raise ValueError("failure_mode は open, closed, local-memory-fallback のいずれかである必要があります。")

        self._client = client
        self._prefix = prefix
        self._key_hash = key_hash
        self._failure_mode = failure_mode
        self._fallback_backend = fallback_backend or InMemoryBackend()
        self._time_provider = time.time

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        prefix: str = "rbl",
        key_hash: bool = False,
        failure_mode: FailureMode = "open",
        fallback_backend: CounterBackend | None = None,
        **kwargs: Any,
    ) -> "RedisBackend":
        kwargs.setdefault("decode_responses", True)
        client = Redis.from_url(url, **kwargs)
        return cls(
            client,
            prefix=prefix,
            key_hash=key_hash,
            failure_mode=failure_mode,
            fallback_backend=fallback_backend,
        )

    async def record_hit(
        self,
        request_key: str,
        handler_name: str,
        rule_index: int,
        window_seconds: int,
    ) -> HitResult:
        counter_key = self._build_counter_key(request_key, handler_name, rule_index)

        try:
            result = await self._client.eval(
                SLIDING_WINDOW_SCRIPT,
                1,
                counter_key,
                str(window_seconds),
                uuid.uuid4().hex,
            )
        except Exception as exc:
            return await self._handle_failure(exc, request_key, handler_name, rule_index, window_seconds)

        return self._parse_hit_result(result)

    async def cleanup_expired(self, active_rules) -> None:
        if self._failure_mode == "local-memory-fallback":
            await self._fallback_backend.cleanup_expired(active_rules)

    def _build_counter_key(self, request_key: str, handler_name: str, rule_index: int) -> str:
        key_tail = request_key
        if self._key_hash:
            key_tail = hashlib.sha256(request_key.encode("utf-8")).hexdigest()
        return f"{self._prefix}:{handler_name}:{rule_index}:{key_tail}"

    async def _handle_failure(
        self,
        exc: Exception,
        request_key: str,
        handler_name: str,
        rule_index: int,
        window_seconds: int,
    ) -> HitResult:
        if self._failure_mode == "open":
            return HitResult(hit_count=0, oldest_timestamp=None, current_timestamp=self._time_provider())

        if self._failure_mode == "local-memory-fallback":
            return await self._fallback_backend.record_hit(request_key, handler_name, rule_index, window_seconds)

        raise CounterBackendUnavailableError("Redis backend is unavailable.") from exc

    def _parse_hit_result(self, result: Any) -> HitResult:
        if not isinstance(result, (list, tuple)) or len(result) < 3:
            raise RuntimeError("Redis script returned an unexpected result.")

        hit_count = int(result[0])
        oldest_raw = self._to_text(result[1])
        current_raw = self._to_text(result[2])

        return HitResult(
            hit_count=hit_count,
            oldest_timestamp=float(oldest_raw) if oldest_raw else None,
            current_timestamp=float(current_raw),
        )

    def _to_text(self, value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        if value is None:
            return ""
        return str(value)
