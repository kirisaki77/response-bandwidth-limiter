import os
import uuid

import pytest

from response_bandwidth_limiter import Reject, Rule
from response_bandwidth_limiter.backend import CounterBackendUnavailableError, InMemoryBackend
from response_bandwidth_limiter.policy import PolicyEvaluator

try:
    from redis.asyncio import Redis
    from response_bandwidth_limiter import RedisBackend
except ImportError:
    Redis = None
    RedisBackend = None


pytestmark = pytest.mark.skipif(RedisBackend is None, reason="redis dependency is not installed")


class FakeRedisClient:
    def __init__(self, result=None, error: Exception | None = None):
        self.result = result or [1, "1.0", "1.0"]
        self.error = error
        self.calls = []

    async def eval(self, script, numkeys, *args):
        self.calls.append({"script": script, "numkeys": numkeys, "args": args})
        if self.error is not None:
            raise self.error
        return self.result


@pytest.mark.asyncio
async def test_redis_backend_uses_raw_request_key_tail():
    client = FakeRedisClient(result=[1, "10.0", "10.0"])
    backend = RedisBackend(client)

    await backend.record_hit("203.0.113.10:tenant-a", "download", 2, 60)

    assert client.calls[0]["args"][0] == "rbl:download:2:203.0.113.10:tenant-a"


@pytest.mark.asyncio
async def test_redis_backend_fail_open_returns_non_matching_hit_result():
    client = FakeRedisClient(error=RuntimeError("redis down"))
    backend = RedisBackend(client, failure_mode="open")

    result = await backend.record_hit("client-a", "download", 0, 60)

    assert result.hit_count == 0
    assert result.oldest_timestamp is None


@pytest.mark.asyncio
async def test_redis_backend_fail_closed_raises_unavailable_error():
    client = FakeRedisClient(error=RuntimeError("redis down"))
    backend = RedisBackend(client, failure_mode="closed")

    with pytest.raises(CounterBackendUnavailableError):
        await backend.record_hit("client-a", "download", 0, 60)


@pytest.mark.asyncio
async def test_redis_backend_local_memory_fallback_uses_in_memory_backend():
    fallback_backend = InMemoryBackend(time_provider=lambda: 1.0)
    client = FakeRedisClient(error=RuntimeError("redis down"))
    backend = RedisBackend(client, failure_mode="local-memory-fallback", fallback_backend=fallback_backend)

    first = await backend.record_hit("client-a", "download", 0, 60)
    second = await backend.record_hit("client-a", "download", 0, 60)

    assert first.hit_count == 1
    assert second.hit_count == 2


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("REDIS_URL"), reason="REDIS_URL is not set")
async def test_redis_backend_shares_counters_across_evaluators():
    raw_client = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    prefix = f"rbl-test-{uuid.uuid4().hex}"
    backend_one = RedisBackend(raw_client, prefix=prefix)
    backend_two = RedisBackend(raw_client, prefix=prefix)
    evaluator_one = PolicyEvaluator(backend=backend_one)
    evaluator_two = PolicyEvaluator(backend=backend_two)
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
