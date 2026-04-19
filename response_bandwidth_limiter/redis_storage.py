import hashlib
import json
import threading
import time
import uuid
from typing import Any, Literal

from .storage import InMemoryStorage, SlidingWindowResult, Storage, StorageUnavailableError

try:
    from redis.asyncio import Redis
except ImportError as exc:
    raise ImportError(
        "RedisStorage requires the optional redis dependency. Install it with `pip install response-bandwidth-limiter[redis]`."
    ) from exc


FailureMode = Literal["open", "closed", "local-memory-fallback"]
ControlFailureMode = Literal["closed", "local-memory-fallback"]

_JSON_PREFIX = "__rbl_json__:"

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


class RedisStorage(Storage):
    def __init__(
        self,
        client: Redis,
        *,
        prefix: str = "rbl",
        key_hash: bool = False,
        counter_failure_mode: FailureMode = "open",
        control_failure_mode: ControlFailureMode = "closed",
        counter_fallback_storage: Storage | None = None,
        control_fallback_storage: Storage | None = None,
    ):
        if client is None:
            raise ValueError("client は必須です。")
        if counter_failure_mode not in {"open", "closed", "local-memory-fallback"}:
            raise ValueError("counter_failure_mode は open, closed, local-memory-fallback のいずれかである必要があります。")
        if control_failure_mode not in {"closed", "local-memory-fallback"}:
            raise ValueError("control_failure_mode は closed, local-memory-fallback のいずれかである必要があります。")

        self._client = client
        self._prefix = prefix
        self._key_hash = key_hash
        self._counter_failure_mode = counter_failure_mode
        self._control_failure_mode = control_failure_mode
        self._counter_fallback_storage = counter_fallback_storage or InMemoryStorage()
        self._control_fallback_storage = control_fallback_storage or self._counter_fallback_storage
        self._time_provider = time.time
        self._closed = False
        self._state_lock = threading.RLock()
        self._handler_generations: dict[str, int] = {}

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        prefix: str = "rbl",
        key_hash: bool = False,
        counter_failure_mode: FailureMode = "open",
        control_failure_mode: ControlFailureMode = "closed",
        counter_fallback_storage: Storage | None = None,
        control_fallback_storage: Storage | None = None,
        **kwargs: Any,
    ) -> "RedisStorage":
        kwargs.setdefault("decode_responses", True)
        client = Redis.from_url(url, **kwargs)
        return cls(
            client,
            prefix=prefix,
            key_hash=key_hash,
            counter_failure_mode=counter_failure_mode,
            control_failure_mode=control_failure_mode,
            counter_fallback_storage=counter_fallback_storage,
            control_fallback_storage=control_fallback_storage,
        )

    async def get(self, key: str) -> Any | None:
        try:
            value = await self._client.get(self._build_data_key(key))
        except Exception as exc:
            return await self._handle_get_failure(key, exc)
        return self._deserialize_value(value)

    async def set(self, key: str, value: Any, expire: int | None = None) -> None:
        try:
            await self._client.set(self._build_data_key(key), self._serialize_value(value), ex=expire)
        except Exception as exc:
            await self._handle_set_failure(key, value, expire, exc)

    async def incr(self, key: str, expire: int | None = None) -> int:
        try:
            if expire is None:
                return int(await self._client.incr(self._build_data_key(key)))

            async with self._client.pipeline(transaction=True) as pipeline:
                pipeline.incr(self._build_data_key(key))
                pipeline.expire(self._build_data_key(key), expire)
                result = await pipeline.execute()
            return int(result[0])
        except Exception as exc:
            return await self._handle_incr_failure(key, expire, exc)

    async def delete(self, key: str) -> None:
        try:
            await self._client.delete(self._build_data_key(key))
        except Exception as exc:
            await self._handle_delete_failure(key, exc)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        close = getattr(self._client, "aclose", None)
        if callable(close):
            await close()

        seen_fallbacks: set[int] = set()
        for fallback in (self._counter_fallback_storage, self._control_fallback_storage):
            identifier = id(fallback)
            if identifier in seen_fallbacks:
                continue
            seen_fallbacks.add(identifier)
            await fallback.close()

    async def record_hit(
        self,
        request_key: str,
        handler_name: str,
        rule_index: int,
        window_seconds: int,
    ) -> SlidingWindowResult:
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
            return await self._handle_record_hit_failure(exc, request_key, handler_name, rule_index, window_seconds)

        return self._parse_hit_result(result)

    def cleanup_handler_counters(self, handler_name: str) -> None:
        # Runtime updates are documented as process-local, so this storage
        # switches to a new local counter namespace instead of deleting shared
        # Redis keys that other workers may still rely on.
        with self._state_lock:
            self._handler_generations[handler_name] = self._handler_generations.get(handler_name, 0) + 1

    def cleanup_orphaned_counters(self, active_rules) -> None:
        return None

    def _build_data_key(self, key: str) -> str:
        return f"{self._prefix}:data:{key}"

    def _build_counter_key(self, request_key: str, handler_name: str, rule_index: int) -> str:
        key_tail = request_key
        if self._key_hash:
            key_tail = hashlib.sha256(request_key.encode("utf-8")).hexdigest()
        with self._state_lock:
            generation = self._handler_generations.get(handler_name, 0)

        if generation > 0:
            return f"{self._prefix}:counter:{handler_name}:v{generation}:{rule_index}:{key_tail}"
        return f"{self._prefix}:counter:{handler_name}:{rule_index}:{key_tail}"

    def _serialize_value(self, value: Any) -> Any:
        if isinstance(value, bytes):
            return value
        return f"{_JSON_PREFIX}{json.dumps(value)}"

    def _deserialize_value(self, value: Any) -> Any | None:
        if value is None:
            return None
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        if isinstance(value, str) and value.startswith(_JSON_PREFIX):
            return json.loads(value[len(_JSON_PREFIX):])
        return value

    def _is_control_key(self, key: str) -> bool:
        return key.startswith("ip:")

    def _counter_mode(self) -> FailureMode:
        return self._counter_failure_mode

    def _control_mode(self) -> ControlFailureMode:
        return self._control_failure_mode

    def _fallback_for_key(self, key: str) -> Storage:
        return self._control_fallback_storage if self._is_control_key(key) else self._counter_fallback_storage

    async def _handle_get_failure(self, key: str, exc: Exception) -> Any | None:
        if self._is_control_key(key):
            if self._control_mode() == "local-memory-fallback":
                return await self._control_fallback_storage.get(key)
            raise StorageUnavailableError("Redis control storage is unavailable.") from exc

        if self._counter_mode() == "open":
            return None
        if self._counter_mode() == "local-memory-fallback":
            return await self._counter_fallback_storage.get(key)
        raise StorageUnavailableError("Redis counter storage is unavailable.") from exc

    async def _handle_set_failure(self, key: str, value: Any, expire: int | None, exc: Exception) -> None:
        if self._is_control_key(key):
            if self._control_mode() == "local-memory-fallback":
                await self._control_fallback_storage.set(key, value, expire=expire)
                return
            raise StorageUnavailableError("Redis control storage is unavailable.") from exc

        if self._counter_mode() == "open":
            return
        if self._counter_mode() == "local-memory-fallback":
            await self._counter_fallback_storage.set(key, value, expire=expire)
            return
        raise StorageUnavailableError("Redis counter storage is unavailable.") from exc

    async def _handle_incr_failure(self, key: str, expire: int | None, exc: Exception) -> int:
        if self._is_control_key(key):
            if self._control_mode() == "local-memory-fallback":
                return await self._control_fallback_storage.incr(key, expire=expire)
            raise StorageUnavailableError("Redis control storage is unavailable.") from exc

        if self._counter_mode() == "open":
            return 0
        if self._counter_mode() == "local-memory-fallback":
            return await self._counter_fallback_storage.incr(key, expire=expire)
        raise StorageUnavailableError("Redis counter storage is unavailable.") from exc

    async def _handle_delete_failure(self, key: str, exc: Exception) -> None:
        if self._is_control_key(key):
            if self._control_mode() == "local-memory-fallback":
                await self._control_fallback_storage.delete(key)
                return
            raise StorageUnavailableError("Redis control storage is unavailable.") from exc

        if self._counter_mode() == "open":
            return
        if self._counter_mode() == "local-memory-fallback":
            await self._counter_fallback_storage.delete(key)
            return
        raise StorageUnavailableError("Redis counter storage is unavailable.") from exc

    async def _handle_record_hit_failure(
        self,
        exc: Exception,
        request_key: str,
        handler_name: str,
        rule_index: int,
        window_seconds: int,
    ) -> SlidingWindowResult:
        if self._counter_mode() == "open":
            return SlidingWindowResult(hit_count=0, oldest_timestamp=None, current_timestamp=self._time_provider())

        if self._counter_mode() == "local-memory-fallback":
            return await self._counter_fallback_storage.record_hit(request_key, handler_name, rule_index, window_seconds)

        raise StorageUnavailableError("Redis counter storage is unavailable.") from exc

    def _parse_hit_result(self, result: Any) -> SlidingWindowResult:
        if not isinstance(result, (list, tuple)) or len(result) < 3:
            raise RuntimeError("Redis script returned an unexpected result.")

        hit_count = int(result[0])
        oldest_raw = self._to_text(result[1])
        current_raw = self._to_text(result[2])

        return SlidingWindowResult(
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