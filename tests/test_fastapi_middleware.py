import asyncio
from datetime import timedelta

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse, StreamingResponse

from response_bandwidth_limiter import Delay, Reject, ResponseBandwidthLimiter, ResponseBandwidthLimiterMiddleware, Rule, SlidingWindowResult, Storage, StorageUnavailableError, Throttle


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


def test_fastapi_dynamic_route_resolution_accepts_route_path_identifier():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    limiter.update_route("items/{item_id}", 100)
    app.state.response_bandwidth_limiter = limiter

    @app.get("/items/{item_id}")
    async def read_item(item_id: str):
        return {"item_id": item_id}

    middleware = ResponseBandwidthLimiterMiddleware(app)
    mock_request = Request(scope={"type": "http", "app": app, "path": "/items/123", "method": "GET"})

    assert middleware.get_handler_name(mock_request, "/items/123") == "items/{item_id}"


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


def test_policy_accepts_timedelta_periods():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    limiter.init_app(app)

    @app.get("/timedelta-limited")
    @limiter.limit_rules([Rule(count=1, per=timedelta(seconds=1), action=Reject(detail="too many requests"))])
    async def timedelta_limited(request: Request):
        return PlainTextResponse("ok")

    client = TestClient(app)

    assert client.get("/timedelta-limited").status_code == 200

    rejected = client.get("/timedelta-limited")
    assert rejected.status_code == 429
    assert rejected.json()["detail"] == "too many requests"
    assert rejected.headers["Retry-After"] == "1"


def test_policy_uses_ip_scope_independently():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter(trusted_proxy_headers=True)
    limiter.init_app(app)

    @app.get("/ip-limited")
    @limiter.limit_rules([Rule(count=1, per="second", action=Reject())])
    async def ip_limited(request: Request):
        return PlainTextResponse("ok")

    client = TestClient(app)

    assert client.get("/ip-limited", headers={"X-Forwarded-For": "10.0.0.1"}).status_code == 200
    assert client.get("/ip-limited", headers={"X-Forwarded-For": "10.0.0.1"}).status_code == 429
    assert client.get("/ip-limited", headers={"X-Forwarded-For": "10.0.0.2"}).status_code == 200


def test_policy_default_scope_uses_built_in_client_identifier():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter(trusted_proxy_headers=True)
    limiter.init_app(app)

    @app.get("/default-limited")
    @limiter.limit_rules([Rule(count=1, per="second", action=Reject(detail="default limited"), scope="default")])
    async def default_limited(request: Request):
        return PlainTextResponse("ok")

    client = TestClient(app)

    assert client.get("/default-limited", headers={"X-Api-Key": "alpha", "X-Forwarded-For": "10.0.0.1"}).status_code == 200
    assert client.get("/default-limited", headers={"X-Api-Key": "beta", "X-Forwarded-For": "10.0.0.1"}).status_code == 429
    assert client.get("/default-limited", headers={"X-Api-Key": "alpha", "X-Forwarded-For": "10.0.0.2"}).status_code == 200


def test_policy_uses_explicit_api_key_scope_resolver():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter(trusted_proxy_headers=True)
    limiter.register_scope_resolver("api_key", lambda request: request.headers.get("X-Api-Key", "anonymous"))
    limiter.init_app(app)

    @app.get("/api-key-limited")
    @limiter.limit_rules([Rule(count=1, per="second", action=Reject(detail="api key limited"), scope="api_key")])
    async def api_key_limited(request: Request):
        return PlainTextResponse("ok")

    client = TestClient(app)

    assert client.get("/api-key-limited", headers={"X-Api-Key": "alpha", "X-Forwarded-For": "10.0.0.1"}).status_code == 200
    assert client.get("/api-key-limited", headers={"X-Api-Key": "alpha", "X-Forwarded-For": "10.0.0.2"}).status_code == 429
    assert client.get("/api-key-limited", headers={"X-Api-Key": "beta", "X-Forwarded-For": "10.0.0.1"}).status_code == 200


def test_get_client_identifier_prefers_real_ip_and_falls_back_to_scope_client():
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

    assert middleware._get_client_identifier(real_ip_request, trust_proxy_headers=True) == "192.0.2.10"
    assert middleware._get_client_identifier(scope_client_request) == "198.51.100.7"
    assert middleware._get_client_identifier(unknown_request) == "unknown"


def test_get_client_identifier_ignores_invalid_proxy_header_values():
    middleware = ResponseBandwidthLimiterMiddleware(FastAPI())
    request = Request(scope={
        "type": "http",
        "path": "/",
        "method": "GET",
        "headers": [(b"x-forwarded-for", b"not-an-ip, 203.0.113.5")],
        "client": ("198.51.100.7", 12345),
    })

    assert middleware._get_client_identifier(request, trust_proxy_headers=True) == "203.0.113.5"


def test_get_client_identifier_accepts_ipv6_proxy_headers():
    middleware = ResponseBandwidthLimiterMiddleware(FastAPI())
    request = Request(scope={
        "type": "http",
        "path": "/",
        "method": "GET",
        "headers": [(b"x-forwarded-for", b"bad-value, 2001:db8::10")],
        "client": ("198.51.100.7", 12345),
    })

    assert middleware._get_client_identifier(request, trust_proxy_headers=True) == "2001:db8::10"


def test_get_client_identifier_ignores_proxy_headers_by_default():
    middleware = ResponseBandwidthLimiterMiddleware(FastAPI())
    request = Request(scope={
        "type": "http",
        "path": "/",
        "method": "GET",
        "headers": [(b"x-forwarded-for", b"203.0.113.1")],
        "client": ("198.51.100.7", 12345),
    })

    assert middleware._get_client_identifier(request) == "198.51.100.7"


def test_get_client_ip_uses_real_ip_sources():
    middleware = ResponseBandwidthLimiterMiddleware(FastAPI())
    request = Request(scope={
        "type": "http",
        "path": "/",
        "method": "GET",
        "headers": [(b"x-forwarded-for", b"198.51.100.10")],
        "client": ("203.0.113.10", 12345),
    })

    assert middleware._get_client_ip(request, trust_proxy_headers=True) == "198.51.100.10"


def test_middleware_accepts_injected_dependencies():
    app = FastAPI()
    evaluator = object()
    streamer = object()

    middleware = ResponseBandwidthLimiterMiddleware(app, policy_evaluator=evaluator, response_streamer=streamer)

    assert middleware.policy_evaluator is evaluator
    assert middleware.response_streamer is streamer


def test_limiter_uses_injected_storage_for_policy_evaluation():
    app = FastAPI()

    class RecordingStorage(Storage):
        def __init__(self):
            self.calls = []

        async def get(self, key: str):
            return None

        async def set(self, key: str, value, expire=None):
            return None

        async def incr(self, key: str, expire=None):
            return 0

        async def delete(self, key: str):
            return None

        async def record_hit(self, request_key, handler_name, rule_index, window_seconds):
            self.calls.append((request_key, handler_name, rule_index, window_seconds))
            return SlidingWindowResult(hit_count=1, oldest_timestamp=1.0, current_timestamp=1.0)

    storage = RecordingStorage()
    limiter = ResponseBandwidthLimiter(storage=storage)
    limiter.register_scope_resolver("client", lambda request: "client-1")
    limiter.init_app(app)

    @app.get("/custom-backend")
    @limiter.limit_rules([Rule(count=1, per="second", action=Reject(), scope="client")])
    async def custom_backend(request: Request):
        return PlainTextResponse("ok")

    response = TestClient(app).get("/custom-backend")

    assert response.status_code == 200
    assert storage.calls == [("client-1", "custom_backend", 0, 1)]


def test_policy_scope_ip_uses_real_ip():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter(trusted_proxy_headers=True)
    limiter.init_app(app)

    @app.get("/strict-ip")
    @limiter.limit_rules([Rule(count=1, per="second", action=Reject(detail="ip limited"), scope="ip")])
    async def strict_ip(request: Request):
        return PlainTextResponse("ok")

    client = TestClient(app)

    assert client.get("/strict-ip", headers={"X-Api-Key": "alpha", "X-Forwarded-For": "203.0.113.10"}).status_code == 200
    assert client.get("/strict-ip", headers={"X-Api-Key": "beta", "X-Forwarded-For": "203.0.113.10"}).status_code == 429
    assert client.get("/strict-ip", headers={"X-Api-Key": "alpha", "X-Forwarded-For": "203.0.113.11"}).status_code == 200


def test_policy_uses_registered_custom_scope_resolver():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter(trusted_proxy_headers=True)
    limiter.register_scope_resolver("user", lambda request: request.headers.get("X-User-Id", "anonymous"))
    limiter.init_app(app)

    @app.get("/user-limited")
    @limiter.limit_rules([Rule(count=1, per="second", action=Reject(detail="user limited"), scope="user")])
    async def user_limited(request: Request):
        return PlainTextResponse("ok")

    client = TestClient(app)

    assert client.get("/user-limited", headers={"X-User-Id": "alpha", "X-Forwarded-For": "10.0.0.1"}).status_code == 200
    assert client.get("/user-limited", headers={"X-User-Id": "alpha", "X-Forwarded-For": "10.0.0.2"}).status_code == 429
    assert client.get("/user-limited", headers={"X-User-Id": "beta", "X-Forwarded-For": "10.0.0.1"}).status_code == 200


def test_policy_mixes_ip_and_custom_scopes(recorded_sleep_calls):
    app = FastAPI()
    limiter = ResponseBandwidthLimiter(trusted_proxy_headers=True)
    limiter.register_scope_resolver("user", lambda request: request.headers.get("X-User-Id", "anonymous"))
    limiter.init_app(app)

    @app.get("/mixed-scope")
    @limiter.limit_rules([
        Rule(count=1, per="second", action=Delay(seconds=0.2), scope="user"),
        Rule(count=1, per="second", action=Reject(detail="ip limited"), scope="ip"),
    ])
    async def mixed_scope(request: Request):
        return PlainTextResponse("ok")

    client = TestClient(app)

    assert client.get("/mixed-scope", headers={"X-User-Id": "alpha", "X-Forwarded-For": "10.0.0.1"}).status_code == 200

    rejected = client.get("/mixed-scope", headers={"X-User-Id": "beta", "X-Forwarded-For": "10.0.0.1"})
    assert rejected.status_code == 429
    assert rejected.json()["detail"] == "ip limited"

    delayed = client.get("/mixed-scope", headers={"X-User-Id": "alpha", "X-Forwarded-For": "10.0.0.2"})
    assert delayed.status_code == 200
    assert delayed.text == "ok"
    assert recorded_sleep_calls == pytest.approx([0.2])


def test_custom_scope_resolver_falls_back_to_real_ip_on_error():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter(trusted_proxy_headers=True)

    def raising_resolver(request: Request) -> str:
        raise RuntimeError("user lookup failed")

    limiter.register_scope_resolver("user", raising_resolver)
    limiter.init_app(app)

    @app.get("/custom-fallback")
    @limiter.limit_rules([Rule(count=1, per="second", action=Reject(detail="fallback limited"), scope="user")])
    async def custom_fallback(request: Request):
        return PlainTextResponse("ok")

    client = TestClient(app)

    assert client.get("/custom-fallback", headers={"X-Forwarded-For": "203.0.113.10"}).status_code == 200
    assert client.get("/custom-fallback", headers={"X-Forwarded-For": "203.0.113.10"}).status_code == 429
    assert client.get("/custom-fallback", headers={"X-Forwarded-For": "203.0.113.11"}).status_code == 200


def test_fail_closed_storage_returns_503():
    app = FastAPI()

    class FailingStorage(Storage):
        async def get(self, key: str):
            return None

        async def set(self, key: str, value, expire=None):
            return None

        async def incr(self, key: str, expire=None):
            return 0

        async def delete(self, key: str):
            return None

        async def record_hit(self, request_key, handler_name, rule_index, window_seconds):
            raise StorageUnavailableError("redis down")

    limiter = ResponseBandwidthLimiter(storage=FailingStorage())
    limiter.init_app(app)

    @app.get("/backend-closed")
    @limiter.limit_rules([Rule(count=1, per="second", action=Reject())])
    async def backend_closed(request: Request):
        return PlainTextResponse("ok")

    response = TestClient(app).get("/backend-closed")

    assert response.status_code == 503
    assert response.json()["error"] == "Rate limit backend unavailable"


def test_blocked_ip_uses_real_ip():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter(trusted_proxy_headers=True)
    limiter.init_app(app)

    @app.get("/blocked")
    async def blocked(request: Request):
        return PlainTextResponse("ok")

    async def prepare() -> None:
        await limiter.block_ip("203.0.113.10")

    asyncio.run(prepare())

    response = TestClient(app).get(
        "/blocked",
        headers={"X-Api-Key": "alpha", "X-Forwarded-For": "203.0.113.10"},
    )

    assert response.status_code == 403


def test_allowed_ip_skips_request_count_policy_but_keeps_bandwidth_limit(recorded_limit_calls):
    app = FastAPI()
    limiter = ResponseBandwidthLimiter(trusted_proxy_headers=True)
    limiter.update_route("allowed", 10)
    limiter.init_app(app)

    @app.get("/allowed")
    @limiter.limit_rules([Rule(count=1, per="second", action=Reject())])
    async def allowed(request: Request):
        return PlainTextResponse("x" * 20)

    async def prepare() -> None:
        await limiter.allow_ip("203.0.113.50")

    asyncio.run(prepare())
    client = TestClient(app)

    assert client.get("/allowed", headers={"X-Forwarded-For": "203.0.113.50"}).status_code == 200
    second = client.get("/allowed", headers={"X-Forwarded-For": "203.0.113.50"})

    assert second.status_code == 200
    assert [call["rate"] for call in recorded_limit_calls] == [10, 10]


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


def test_handler_exception_releases_in_flight_counter():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    limiter.update_route("failing", 10)
    limiter.init_app(app, install_signal_handlers=False)

    @app.get("/failing")
    async def failing():
        raise RuntimeError("boom")

    client = TestClient(app)

    with pytest.raises(RuntimeError, match="boom"):
        client.get("/failing")

    assert limiter.shutdown_coordinator.in_flight_count == 0


def test_limit_decorator_preserves_fastapi_endpoint_signature(recorded_limit_calls):
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    limiter.init_app(app)

    @app.get("/signature")
    @limiter.limit(10)
    async def signature_preserved():
        return PlainTextResponse("ok")

    response = TestClient(app).get("/signature")

    assert response.status_code == 200
    assert response.text == "ok"
    assert [call["rate"] for call in recorded_limit_calls] == [10]


def test_non_http_scope_passes_through():
    received_scope = {}

    async def mock_app(scope, receive, send):
        received_scope.update(scope)

    middleware = ResponseBandwidthLimiterMiddleware(mock_app)

    asyncio.run(middleware({"type": "websocket"}, None, None))

    assert received_scope["type"] == "websocket"


def test_empty_body_response_passes_through(recorded_limit_calls):
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    limiter.update_route("empty_response", 100)
    limiter.init_app(app)

    @app.get("/empty")
    async def empty_response():
        return PlainTextResponse("")

    client = TestClient(app)
    response = client.get("/empty")

    assert response.status_code == 200
    assert response.content == b""
    assert recorded_limit_calls == []


def test_policy_delay_applies_to_empty_body_response(recorded_sleep_calls):
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    limiter.init_app(app)

    @app.get("/empty-delay")
    @limiter.limit_rules([Rule(count=1, per="second", action=Delay(seconds=0.2))])
    async def empty_delay(request: Request):
        return PlainTextResponse("")

    client = TestClient(app)

    assert client.get("/empty-delay").status_code == 200
    response = client.get("/empty-delay")

    assert response.status_code == 200
    assert response.content == b""
    assert recorded_sleep_calls == pytest.approx([0.2])


def test_policy_throttle_skips_limiting_for_empty_body(recorded_limit_calls):
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    limiter.init_app(app)

    @app.get("/empty-throttle")
    @limiter.limit_rules([Rule(count=1, per="second", action=Throttle(bytes_per_sec=10))])
    async def empty_throttle(request: Request):
        return PlainTextResponse("")

    client = TestClient(app)

    assert client.get("/empty-throttle").status_code == 200
    response = client.get("/empty-throttle")

    assert response.status_code == 200
    assert response.content == b""
    assert recorded_limit_calls == []


def test_storage_unavailable_on_ip_check_returns_503():
    app = FastAPI()

    class FailingIPStorage(Storage):
        async def get(self, key: str):
            if key.startswith("ip:"):
                raise StorageUnavailableError("redis down")
            return None

        async def set(self, key: str, value, expire=None):
            return None

        async def incr(self, key: str, expire=None):
            return 0

        async def delete(self, key: str):
            return None

    limiter = ResponseBandwidthLimiter(storage=FailingIPStorage(), trusted_proxy_headers=True)
    limiter.init_app(app)

    @app.get("/ip-check")
    async def ip_check(request: Request):
        return PlainTextResponse("ok")

    response = TestClient(app).get("/ip-check", headers={"X-Forwarded-For": "203.0.113.10"})

    assert response.status_code == 503
    assert response.json()["error"] == "Rate limit backend unavailable"


def test_custom_scope_resolver_returning_none_falls_back_to_ip():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter(trusted_proxy_headers=True)
    limiter.register_scope_resolver("token", lambda request: None)
    limiter.init_app(app)

    @app.get("/none-scope")
    @limiter.limit_rules([Rule(count=1, per="second", action=Reject(detail="none scope limited"), scope="token")])
    async def none_scope(request: Request):
        return PlainTextResponse("ok")

    client = TestClient(app)

    assert client.get("/none-scope", headers={"X-Forwarded-For": "203.0.113.10"}).status_code == 200
    assert client.get("/none-scope", headers={"X-Forwarded-For": "203.0.113.10"}).status_code == 429
    assert client.get("/none-scope", headers={"X-Forwarded-For": "203.0.113.11"}).status_code == 200


def test_custom_scope_resolver_returning_empty_string_falls_back_to_ip():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter(trusted_proxy_headers=True)
    limiter.register_scope_resolver("token", lambda request: "")
    limiter.init_app(app)

    @app.get("/empty-scope")
    @limiter.limit_rules([Rule(count=1, per="second", action=Reject(detail="empty scope limited"), scope="token")])
    async def empty_scope(request: Request):
        return PlainTextResponse("ok")

    client = TestClient(app)

    assert client.get("/empty-scope", headers={"X-Forwarded-For": "203.0.113.20"}).status_code == 200
    assert client.get("/empty-scope", headers={"X-Forwarded-For": "203.0.113.20"}).status_code == 429
    assert client.get("/empty-scope", headers={"X-Forwarded-For": "203.0.113.21"}).status_code == 200


def test_starlette_mixed_ip_and_custom_scopes():
    from starlette.applications import Starlette
    from starlette.routing import Route

    limiter = ResponseBandwidthLimiter(trusted_proxy_headers=True)
    limiter.register_scope_resolver("tenant", lambda request: request.headers.get("X-Tenant", "default-tenant"))

    async def mixed(request):
        return PlainTextResponse("ok")

    mixed = limiter.limit_rules([
        Rule(count=1, per="second", action=Reject(detail="ip limited"), scope="ip"),
        Rule(count=2, per="second", action=Reject(detail="tenant limited"), scope="tenant"),
    ])(mixed)

    app = Starlette(routes=[Route("/mixed", endpoint=mixed)])
    limiter.init_app(app)

    client = TestClient(app)

    # 同一IP、異なるテナント — IPルールで制限される
    assert client.get("/mixed", headers={"X-Forwarded-For": "10.0.0.1", "X-Tenant": "a"}).status_code == 200
    r = client.get("/mixed", headers={"X-Forwarded-For": "10.0.0.1", "X-Tenant": "b"})
    assert r.status_code == 429
    assert r.json()["detail"] == "ip limited"
