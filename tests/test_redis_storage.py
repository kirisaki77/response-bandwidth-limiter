import os
import uuid

import pytest

from response_bandwidth_limiter import InMemoryStorage, RedisStorage, Reject, Rule, SlidingWindowResult, StorageUnavailableError
from response_bandwidth_limiter.policy import PolicyEvaluator

try:
    from redis.asyncio import Redis
except ImportError:
    Redis = None


pytestmark = pytest.mark.skipif(Redis is None, reason="redis dependency is not installed")


class FakePipeline:
    def __init__(self, client):
        self._client = client
        self._operations = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def incr(self, key):
        self._operations.append(("incr", key))
        return self

    def expire(self, key, expire):
        self._operations.append(("expire", key, expire))
        return self

    async def execute(self):
        results = []
        for operation in self._operations:
            if operation[0] == "incr":
                results.append(await self._client.incr(operation[1]))
            elif operation[0] == "expire":
                self._client.expirations[operation[1]] = operation[2]
                results.append(True)
        return results


class FakeRedisClient:
    def __init__(self, result=None, error: Exception | None = None):
        self.result = result or [1, "1.0", "1.0"]
        self.error = error
        self.calls = []
        self.data = {}
        self.expirations = {}

    async def eval(self, script, numkeys, *args):
        self.calls.append({"script": script, "numkeys": numkeys, "args": args})
        if self.error is not None:
            raise self.error
        return self.result

    async def get(self, key):
        if self.error is not None:
            raise self.error
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        if self.error is not None:
            raise self.error
        self.data[key] = value
        if ex is not None:
            self.expirations[key] = ex
        return True

    async def incr(self, key):
        if self.error is not None:
            raise self.error
        current = int(self.data.get(key, 0)) + 1
        self.data[key] = current
        return current

    async def delete(self, key):
        if self.error is not None:
            raise self.error
        self.data.pop(key, None)
        self.expirations.pop(key, None)
        return 1

    def pipeline(self, transaction=True):
        return FakePipeline(self)

    async def aclose(self):
        return None


class FakeRedisCounterClient:
    def __init__(self):
        self.calls = []
        self.hit_counts = {}
        self.current_time = 0.0

    async def eval(self, script, numkeys, *args):
        self.calls.append({"script": script, "numkeys": numkeys, "args": args})
        counter_key = args[0]
        hit_count = self.hit_counts.get(counter_key, 0) + 1
        self.hit_counts[counter_key] = hit_count
        self.current_time += 1.0
        oldest_timestamp = self.current_time - hit_count + 1.0
        return [hit_count, str(oldest_timestamp), str(self.current_time)]

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_redis_storage_uses_raw_request_key_tail():
    client = FakeRedisClient(result=[1, "10.0", "10.0"])
    storage = RedisStorage(client)

    await storage.record_hit("203.0.113.10:tenant-a", "download", 2, 60)

    assert client.calls[0]["args"][0] == "rbl:counter:download:2:203.0.113.10:tenant-a"


@pytest.mark.asyncio
async def test_redis_storage_cleanup_handler_counters_uses_new_local_namespace():
    client = FakeRedisCounterClient()
    storage = RedisStorage(client)

    first = await storage.record_hit("client-a", "download", 0, 60)
    second = await storage.record_hit("client-a", "download", 0, 60)
    storage.cleanup_handler_counters("download")
    third = await storage.record_hit("client-a", "download", 0, 60)

    assert first.hit_count == 1
    assert second.hit_count == 2
    assert third.hit_count == 1
    assert client.calls[0]["args"][0] == "rbl:counter:download:0:client-a"
    assert client.calls[2]["args"][0] == "rbl:counter:download:v1:0:client-a"


@pytest.mark.asyncio
async def test_redis_storage_cleanup_handler_counters_stays_process_local():
    client = FakeRedisCounterClient()
    storage_one = RedisStorage(client)
    storage_two = RedisStorage(client)

    first = await storage_one.record_hit("client-a", "download", 0, 60)
    storage_one.cleanup_handler_counters("download")
    second = await storage_one.record_hit("client-a", "download", 0, 60)
    third = await storage_two.record_hit("client-a", "download", 0, 60)

    assert first.hit_count == 1
    assert second.hit_count == 1
    assert third.hit_count == 2


@pytest.mark.asyncio
async def test_redis_storage_supports_generic_get_set_incr_delete():
    client = FakeRedisClient()
    storage = RedisStorage(client)

    await storage.set("ip:block:203.0.113.10", {"blocked": True}, expire=30)
    value = await storage.get("ip:block:203.0.113.10")
    count = await storage.incr("counter:test", expire=10)
    await storage.delete("ip:block:203.0.113.10")

    assert value == {"blocked": True}
    assert count == 1
    assert await storage.get("ip:block:203.0.113.10") is None


@pytest.mark.asyncio
async def test_redis_storage_counter_fail_open_returns_non_matching_hit_result():
    client = FakeRedisClient(error=RuntimeError("redis down"))
    storage = RedisStorage(client, counter_failure_mode="open")

    result = await storage.record_hit("client-a", "download", 0, 60)

    assert result.hit_count == 0
    assert result.oldest_timestamp is None


@pytest.mark.asyncio
async def test_redis_storage_counter_fail_closed_raises_unavailable_error():
    client = FakeRedisClient(error=RuntimeError("redis down"))
    storage = RedisStorage(client, counter_failure_mode="closed")

    with pytest.raises(StorageUnavailableError):
        await storage.record_hit("client-a", "download", 0, 60)


@pytest.mark.asyncio
async def test_redis_storage_counter_local_memory_fallback_uses_in_memory_storage():
    fallback_storage = InMemoryStorage(time_provider=lambda: 1.0)
    client = FakeRedisClient(error=RuntimeError("redis down"))
    storage = RedisStorage(client, counter_failure_mode="local-memory-fallback", counter_fallback_storage=fallback_storage)

    first = await storage.record_hit("client-a", "download", 0, 60)
    second = await storage.record_hit("client-a", "download", 0, 60)

    assert first.hit_count == 1
    assert second.hit_count == 2


@pytest.mark.asyncio
async def test_redis_storage_control_failure_mode_is_not_fail_open():
    client = FakeRedisClient(error=RuntimeError("redis down"))
    storage = RedisStorage(client, counter_failure_mode="open", control_failure_mode="closed")

    with pytest.raises(StorageUnavailableError):
        await storage.get("ip:block:203.0.113.10")


@pytest.mark.asyncio
async def test_redis_storage_close_closes_client_and_fallback_storages():
    class TrackingStorage(InMemoryStorage):
        def __init__(self):
            super().__init__()
            self.close_calls = 0

        async def close(self):
            self.close_calls += 1

    class TrackingClient(FakeRedisClient):
        def __init__(self):
            super().__init__()
            self.aclose_calls = 0

        async def aclose(self):
            self.aclose_calls += 1

    client = TrackingClient()
    counter_fallback = TrackingStorage()
    control_fallback = TrackingStorage()
    storage = RedisStorage(
        client,
        counter_failure_mode="local-memory-fallback",
        control_failure_mode="local-memory-fallback",
        counter_fallback_storage=counter_fallback,
        control_fallback_storage=control_fallback,
    )

    await storage.close()

    assert client.aclose_calls == 1
    assert counter_fallback.close_calls == 1
    assert control_fallback.close_calls == 1


@pytest.mark.asyncio
async def test_redis_storage_close_is_idempotent():
    client = FakeRedisClient()
    storage = RedisStorage(client)

    await storage.close()
    await storage.close()


@pytest.mark.asyncio
async def test_redis_storage_key_hash_hashes_request_key_tail():
    import hashlib

    client = FakeRedisClient(result=[1, "10.0", "10.0"])
    storage = RedisStorage(client, key_hash=True)

    await storage.record_hit("203.0.113.10:tenant-a", "download", 2, 60)

    expected_hash = hashlib.sha256("203.0.113.10:tenant-a".encode("utf-8")).hexdigest()
    assert client.calls[0]["args"][0] == f"rbl:counter:download:2:{expected_hash}"


@pytest.mark.asyncio
async def test_redis_storage_counter_key_get_fail_open_returns_none():
    client = FakeRedisClient(error=RuntimeError("redis down"))
    storage = RedisStorage(client, counter_failure_mode="open")

    result = await storage.get("counter:test")

    assert result is None


@pytest.mark.asyncio
async def test_redis_storage_counter_key_set_fail_open_is_silent():
    client = FakeRedisClient(error=RuntimeError("redis down"))
    storage = RedisStorage(client, counter_failure_mode="open")

    await storage.set("counter:test", "value")


@pytest.mark.asyncio
async def test_redis_storage_counter_key_incr_fail_open_returns_zero():
    client = FakeRedisClient(error=RuntimeError("redis down"))
    storage = RedisStorage(client, counter_failure_mode="open")

    result = await storage.incr("counter:test")

    assert result == 0


@pytest.mark.asyncio
async def test_redis_storage_counter_key_delete_fail_open_is_silent():
    client = FakeRedisClient(error=RuntimeError("redis down"))
    storage = RedisStorage(client, counter_failure_mode="open")

    await storage.delete("counter:test")


@pytest.mark.asyncio
async def test_redis_storage_counter_key_fail_closed_raises():
    client = FakeRedisClient(error=RuntimeError("redis down"))
    storage = RedisStorage(client, counter_failure_mode="closed")

    with pytest.raises(StorageUnavailableError):
        await storage.get("counter:test")

    with pytest.raises(StorageUnavailableError):
        await storage.set("counter:test", "value")

    with pytest.raises(StorageUnavailableError):
        await storage.incr("counter:test")

    with pytest.raises(StorageUnavailableError):
        await storage.delete("counter:test")


def test_redis_storage_rejects_none_client():
    with pytest.raises(ValueError):
        RedisStorage(None)


def test_redis_storage_rejects_invalid_counter_failure_mode():
    client = FakeRedisClient()
    with pytest.raises(ValueError):
        RedisStorage(client, counter_failure_mode="invalid")


def test_redis_storage_rejects_invalid_control_failure_mode():
    client = FakeRedisClient()
    with pytest.raises(ValueError):
        RedisStorage(client, control_failure_mode="open")


@pytest.mark.asyncio
async def test_redis_storage_control_local_memory_fallback_for_get_set_delete():
    fallback = InMemoryStorage()
    client = FakeRedisClient(error=RuntimeError("redis down"))
    storage = RedisStorage(
        client,
        counter_failure_mode="closed",
        control_failure_mode="local-memory-fallback",
        control_fallback_storage=fallback,
    )

    await storage.set("ip:block:203.0.113.10", "1", expire=60)
    assert await storage.get("ip:block:203.0.113.10") == "1"

    await storage.delete("ip:block:203.0.113.10")
    assert await storage.get("ip:block:203.0.113.10") is None


@pytest.mark.asyncio
async def test_redis_storage_control_local_memory_fallback_for_incr():
    fallback = InMemoryStorage()
    client = FakeRedisClient(error=RuntimeError("redis down"))
    storage = RedisStorage(
        client,
        counter_failure_mode="closed",
        control_failure_mode="local-memory-fallback",
        control_fallback_storage=fallback,
    )

    result = await storage.incr("ip:counter:test", expire=10)
    assert result == 1


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("REDIS_URL"), reason="REDIS_URL is not set")
async def test_redis_storage_shares_counters_across_evaluators():
    raw_client = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    prefix = f"rbl-test-{uuid.uuid4().hex}"
    storage_one = RedisStorage(raw_client, prefix=prefix)
    storage_two = RedisStorage(raw_client, prefix=prefix)
    evaluator_one = PolicyEvaluator(storage=storage_one)
    evaluator_two = PolicyEvaluator(storage=storage_two)
    rule = Rule(count=1, per="second", action=Reject())

    try:
        assert await evaluator_one.evaluate("203.0.113.10", "download", [rule]) is None

        result = await evaluator_two.evaluate("203.0.113.10", "download", [rule])

        assert result is not None
        assert isinstance(result.rule.action, Reject)
    finally:
        keys = await raw_client.keys(f"{prefix}:*")
        if keys:
            await raw_client.delete(*keys)
        await raw_client.aclose()