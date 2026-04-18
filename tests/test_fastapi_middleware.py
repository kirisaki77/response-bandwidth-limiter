import asyncio

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse, StreamingResponse

from response_bandwidth_limiter import Delay, Reject, ResponseBandwidthLimiter, ResponseBandwidthLimiterMiddleware, Rule, Throttle


def test_fastapi_middleware(recorded_limit_calls):
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    limiter.update_route("read_test", 100)
    limiter.init_app(app)

    @app.get("/test")
    async def read_test():
        return PlainTextResponse("a" * 300)

    response = TestClient(app).get("/test")

    assert response.status_code == 200
    assert len(response.content) == 300
    assert [call["rate"] for call in recorded_limit_calls] == [100]


def test_bandwidth_limit_effectiveness(recorded_limit_calls):
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    fast_limit = 5000
    slow_limit = 500

    limiter.update_route("fast_response", fast_limit)
    limiter.update_route("slow_response", slow_limit)
    limiter.init_app(app)

    data_size = 10000

    @app.get("/fast")
    async def fast_response():
        return PlainTextResponse("a" * data_size)

    @app.get("/slow")
    async def slow_response():
        return PlainTextResponse("b" * data_size)

    client = TestClient(app)
    fast_response = client.get("/fast")
    slow_response = client.get("/slow")

    assert len(fast_response.content) == data_size
    assert len(slow_response.content) == data_size
    assert [call["rate"] for call in recorded_limit_calls] == [fast_limit, slow_limit]


def test_streaming_bandwidth_limit(recorded_limit_calls):
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    limiter.update_route("fast_stream", 2000)
    limiter.update_route("slow_stream", 500)
    limiter.init_app(app)

    chunk_size = 1000
    chunks = 5

    async def fast_generator():
        for _ in range(chunks):
            yield ("a" * chunk_size).encode("utf-8")

    async def slow_generator():
        for _ in range(chunks):
            yield ("b" * chunk_size).encode("utf-8")

    @app.get("/fast-stream")
    async def fast_stream():
        return StreamingResponse(fast_generator())

    @app.get("/slow-stream")
    async def slow_stream():
        return StreamingResponse(slow_generator())

    client = TestClient(app)
    fast_response = client.get("/fast-stream")
    slow_response = client.get("/slow-stream")

    assert len(fast_response.content) == chunk_size * chunks
    assert len(slow_response.content) == chunk_size * chunks
    assert [call["rate"] for call in recorded_limit_calls] == ([2000] * chunks) + ([500] * chunks)


def test_yield_limited_chunks_splits_chunk_by_rate(recorded_sleep_calls):
    middleware = ResponseBandwidthLimiterMiddleware(FastAPI())

    async def collect_parts():
        parts = []
        async for part in middleware._yield_limited_chunks(b"x" * 25, 10):
            parts.append(part)
        return parts

    parts = asyncio.run(collect_parts())

    assert [len(part) for part in parts] == [10, 10, 5]
    assert b"".join(parts) == b"x" * 25
    assert recorded_sleep_calls == pytest.approx([1.0, 1.0, 0.5])

def test_fastapi_route_resolution():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    limiter.update_route("custom_name", 100)
    app.state.response_bandwidth_limiter = limiter
    middleware = ResponseBandwidthLimiterMiddleware(app)
    
    # モックリクエストを作成
    mock_request = Request(scope={"type": "http", "app": app, "path": "/not-exist", "method": "GET"})
    
    assert middleware.get_handler_name(mock_request, "/not-exist") is None
    
    @app.get("/test", name="custom_name")
    async def read_test():
        return {"hello": "world"}
    
    mock_request = Request(scope={"type": "http", "app": app, "path": "/test", "method": "GET"})
    assert middleware.get_handler_name(mock_request, "/test") == "custom_name"

    limiter.update_route("read_test", 200)
    middleware = ResponseBandwidthLimiterMiddleware(app)
    assert middleware.get_handler_name(mock_request, "/test") == "read_test"


def test_fastapi_dynamic_route_resolution():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    limiter.update_route("read_item", 100)
    app.state.response_bandwidth_limiter = limiter

    @app.get("/items/{item_id}")
    async def read_item(item_id: str):
        return {"item_id": item_id}

    middleware = ResponseBandwidthLimiterMiddleware(app)
    mock_request = Request(scope={"type": "http", "app": app, "path": "/items/123", "method": "GET"})
    assert middleware.get_handler_name(mock_request, "/items/123") == "read_item"


def test_fastapi_route_resolution_accepts_suffix_based_limit_name():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    limiter.update_route("download", 100)
    app.state.response_bandwidth_limiter = limiter

    @app.get("/download")
    async def download_response():
        return {"ok": True}

    middleware = ResponseBandwidthLimiterMiddleware(app)
    mock_request = Request(scope={"type": "http", "app": app, "path": "/download", "method": "GET"})

    assert middleware.get_handler_name(mock_request, "/download") == "download"

def test_plain_response_headers_are_preserved():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    limiter.update_route("read_test", 200)
    limiter.init_app(app)

    @app.get("/test")
    async def read_test():
        response = PlainTextResponse("payload", headers={"X-Test": "ok"})
        response.set_cookie("session", "value")
        return response

    client = TestClient(app)
    response = client.get("/test")

    assert response.status_code == 200
    assert response.headers["X-Test"] == "ok"
    assert response.cookies.get("session") == "value"

def test_small_plain_response_is_delayed_before_first_chunk(recorded_sleep_calls):
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    limiter.update_route("slow_response", 10)
    limiter.init_app(app)

    @app.get("/slow")
    async def slow_response():
        return PlainTextResponse("x" * 20)

    client = TestClient(app)
    response = client.get("/slow")

    assert response.status_code == 200
    assert response.text == "x" * 20
    assert recorded_sleep_calls == pytest.approx([1.0, 1.0])


def test_policy_rejects_after_threshold_per_ip():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    limiter.init_app(app)

    @app.get("/limited")
    @limiter.limit_rules([Rule(count=2, per="second", action=Reject(detail="too many requests"))])
    async def limited(request: Request):
        return PlainTextResponse("ok")

    client = TestClient(app)

    assert client.get("/limited").status_code == 200
    assert client.get("/limited").status_code == 200

    rejected = client.get("/limited")
    assert rejected.status_code == 429
    assert rejected.json()["detail"] == "too many requests"
    assert rejected.headers["Retry-After"] == "1"


def test_policy_uses_ip_scope_independently():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    limiter.init_app(app)

    @app.get("/ip-limited")
    @limiter.limit_rules([Rule(count=1, per="second", action=Reject())])
    async def ip_limited(request: Request):
        return PlainTextResponse("ok")

    client = TestClient(app)

    assert client.get("/ip-limited", headers={"X-Forwarded-For": "10.0.0.1"}).status_code == 200
    assert client.get("/ip-limited", headers={"X-Forwarded-For": "10.0.0.1"}).status_code == 429
    assert client.get("/ip-limited", headers={"X-Forwarded-For": "10.0.0.2"}).status_code == 200


def test_policy_uses_key_func_instead_of_ip_headers():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter(key_func=lambda request: request.headers.get("X-Api-Key", "anonymous"))
    limiter.init_app(app)

    @app.get("/key-limited")
    @limiter.limit_rules([Rule(count=1, per="second", action=Reject(detail="key limited"))])
    async def key_limited(request: Request):
        return PlainTextResponse("ok")

    client = TestClient(app)

    assert client.get("/key-limited", headers={"X-Api-Key": "alpha", "X-Forwarded-For": "10.0.0.1"}).status_code == 200
    assert client.get("/key-limited", headers={"X-Api-Key": "alpha", "X-Forwarded-For": "10.0.0.2"}).status_code == 429
    assert client.get("/key-limited", headers={"X-Api-Key": "beta", "X-Forwarded-For": "10.0.0.1"}).status_code == 200


def test_get_request_key_prefers_real_ip_and_falls_back_to_scope_client():
    middleware = ResponseBandwidthLimiterMiddleware(FastAPI())

    real_ip_request = Request(scope={
        "type": "http",
        "path": "/",
        "method": "GET",
        "headers": [(b"x-real-ip", b"192.0.2.10")],
    })
    scope_client_request = Request(scope={
        "type": "http",
        "path": "/",
        "method": "GET",
        "headers": [],
        "client": ("198.51.100.7", 12345),
    })
    unknown_request = Request(scope={
        "type": "http",
        "path": "/",
        "method": "GET",
        "headers": [],
    })

    assert middleware._get_request_key(real_ip_request, None) == "192.0.2.10"
    assert middleware._get_request_key(scope_client_request, None) == "198.51.100.7"
    assert middleware._get_request_key(unknown_request, None) == "unknown"


def test_policy_applies_delay_before_handler_execution(recorded_sleep_calls):
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    limiter.init_app(app)

    @app.get("/delay")
    @limiter.limit_rules([Rule(count=1, per="second", action=Delay(seconds=0.2))])
    async def delayed(request: Request):
        return PlainTextResponse("ok")

    client = TestClient(app)

    assert client.get("/delay").status_code == 200
    response = client.get("/delay")

    assert response.status_code == 200
    assert recorded_sleep_calls == pytest.approx([0.2])


def test_policy_throttle_overrides_response_rate_after_threshold(recorded_limit_calls):
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    limiter.init_app(app)

    @app.get("/policy-throttle")
    @limiter.limit_rules([Rule(count=1, per="second", action=Throttle(bytes_per_sec=10))])
    async def policy_throttle(request: Request):
        return PlainTextResponse("x" * 20)

    client = TestClient(app)

    first = client.get("/policy-throttle")
    assert first.status_code == 200

    second = client.get("/policy-throttle")

    assert second.status_code == 200
    assert second.text == "x" * 20
    assert [call["rate"] for call in recorded_limit_calls] == [10]


def test_update_route_supports_runtime_bandwidth_changes(recorded_limit_calls):
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    limiter.init_app(app)

    @app.get("/legacy")
    async def legacy():
        return PlainTextResponse("x" * 20)

    limiter.update_route("legacy", 10)

    response = TestClient(app).get("/legacy")

    assert response.status_code == 200
    assert [call["rate"] for call in recorded_limit_calls] == [10]
