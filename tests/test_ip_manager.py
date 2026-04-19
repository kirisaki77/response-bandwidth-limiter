import asyncio

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse

from response_bandwidth_limiter import InMemoryStorage, Reject, ResponseBandwidthLimiter, Rule
from response_bandwidth_limiter.ip_manager import IPManager


@pytest.mark.asyncio
async def test_ip_manager_supports_block_allow_and_ttl():
    now = [0.0]
    storage = InMemoryStorage(time_provider=lambda: now[0])
    manager = IPManager(storage)

    await manager.block_ip("203.0.113.10", duration=1)
    await manager.allow_ip("203.0.113.11")

    assert await manager.is_blocked("203.0.113.10") is True
    assert await manager.is_allowed("203.0.113.11") is True

    now[0] = 2.0
    assert await manager.is_blocked("203.0.113.10") is False


@pytest.mark.asyncio
async def test_ip_manager_rejects_invalid_ip_addresses():
    manager = IPManager(InMemoryStorage())

    with pytest.raises(ValueError):
        await manager.block_ip("not-an-ip")


def test_blocked_ip_returns_403_from_middleware():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter(trusted_proxy_headers=True)
    limiter.init_app(app)

    @app.get("/blocked")
    async def blocked(request: Request):
        return PlainTextResponse("ok")

    async def prepare() -> None:
        await limiter.block_ip("203.0.113.10")

    asyncio.run(prepare())

    response = TestClient(app).get("/blocked", headers={"X-Forwarded-For": "203.0.113.10"})

    assert response.status_code == 403


def test_allowed_ip_skips_request_count_policy():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter(trusted_proxy_headers=True)
    limiter.init_app(app)

    @app.get("/allowed")
    @limiter.limit_rules([Rule(count=1, per="second", action=Reject(detail="too many requests"))])
    async def allowed(request: Request):
        return PlainTextResponse("ok")

    async def prepare() -> None:
        await limiter.allow_ip("203.0.113.20")

    asyncio.run(prepare())
    client = TestClient(app)

    assert client.get("/allowed", headers={"X-Forwarded-For": "203.0.113.20"}).status_code == 200
    assert client.get("/allowed", headers={"X-Forwarded-For": "203.0.113.20"}).status_code == 200
