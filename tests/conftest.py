import pytest

import response_bandwidth_limiter.middleware as middleware_module
from response_bandwidth_limiter.decorator import endpoint_bandwidth_limits
from response_bandwidth_limiter import ResponseBandwidthLimiterMiddleware


@pytest.fixture(autouse=True)
def clear_endpoint_bandwidth_limits():
    endpoint_bandwidth_limits.clear()
    yield
    endpoint_bandwidth_limits.clear()


@pytest.fixture
def recorded_limit_calls(monkeypatch):
    calls = []

    async def fake_yield_limited_chunks(self, chunk, max_rate):
        calls.append({"chunk": chunk, "size": len(chunk), "rate": max_rate})
        yield chunk

    monkeypatch.setattr(ResponseBandwidthLimiterMiddleware, "_yield_limited_chunks", fake_yield_limited_chunks)
    return calls


@pytest.fixture
def recorded_sleep_calls(monkeypatch):
    calls = []

    async def fake_sleep(duration):
        calls.append(duration)

    monkeypatch.setattr(middleware_module.asyncio, "sleep", fake_sleep)
    return calls