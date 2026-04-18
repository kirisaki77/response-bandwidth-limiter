import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, StreamingResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from response_bandwidth_limiter import Reject, ResponseBandwidthLimiter, ResponseBandwidthLimiterMiddleware, Rule
from starlette.requests import Request

# Starletteのミドルウェアテスト
def test_starlette_middleware(recorded_limit_calls):
    async def test_endpoint(request):
        return PlainTextResponse("a" * 300)
    
    routes = [
        Route("/test", endpoint=test_endpoint, name="test_route"),
    ]
    
    app = Starlette(routes=routes)
    
    # 新しい方法で設定
    app.state.response_bandwidth_limits = {"test_route": 100}
    app.add_middleware(ResponseBandwidthLimiterMiddleware)
    
    client = TestClient(app)
    response = client.get("/test")
    
    assert response.status_code == 200
    assert len(response.content) == 300
    assert [call["rate"] for call in recorded_limit_calls] == [100]

# 帯域制限の実効性テスト（Starlette版）
def test_starlette_bandwidth_limit_effectiveness(recorded_limit_calls):
    # 高速経路のハンドラー
    async def fast_response(request):
        return PlainTextResponse("a" * 10000)
    
    # 低速経路のハンドラー
    async def slow_response(request):
        return PlainTextResponse("b" * 10000)
    
    # ルートを定義
    routes = [
        Route("/fast", endpoint=fast_response, name="fast_response"),
        Route("/slow", endpoint=slow_response, name="slow_response"),
    ]
    
    app = Starlette(routes=routes)
    
    # 異なる帯域制限を設定
    fast_limit = 5000
    slow_limit = 500
    
    # 新しい方法で制限を設定
    app.state.response_bandwidth_limits = {
        "fast_response": fast_limit,
        "slow_response": slow_limit
    }
    app.add_middleware(ResponseBandwidthLimiterMiddleware)
    
    client = TestClient(app)

    fast_response = client.get("/fast")
    slow_response = client.get("/slow")
    
    # レスポンス検証
    assert len(fast_response.content) == 10000
    assert len(slow_response.content) == 10000

    assert [call["rate"] for call in recorded_limit_calls] == [fast_limit, slow_limit]

# ストリーミングレスポンスでの帯域制限テスト（Starlette版）
def test_starlette_streaming_bandwidth_limit(recorded_limit_calls):
    chunk_size = 1000  # 各チャンクのサイズ
    chunks = 5  # チャンク数
    
    async def fast_generator():
        for i in range(chunks):
            yield f"{'a' * chunk_size}".encode("utf-8")
    
    async def slow_generator():
        for i in range(chunks):
            yield f"{'b' * chunk_size}".encode("utf-8")
    
    async def fast_stream(request):
        return StreamingResponse(fast_generator())
    
    async def slow_stream(request):
        return StreamingResponse(slow_generator())
    
    routes = [
        Route("/fast-stream", endpoint=fast_stream, name="fast_stream"),
        Route("/slow-stream", endpoint=slow_stream, name="slow_stream"),
    ]
    
    app = Starlette(routes=routes)
    
    # 異なる帯域制限を設定
    app.state.response_bandwidth_limits = {
        "fast_stream": 2000,
        "slow_stream": 500
    }
    app.add_middleware(ResponseBandwidthLimiterMiddleware)
    
    client = TestClient(app)

    fast_response = client.get("/fast-stream")
    slow_response = client.get("/slow-stream")
    
    # レスポンスデータの検証
    assert len(fast_response.content) == chunk_size * chunks
    assert len(slow_response.content) == chunk_size * chunks

    assert [call["rate"] for call in recorded_limit_calls] == ([2000] * chunks) + ([500] * chunks)

# Starletteのルート解決テスト
def test_starlette_route_resolution():
    async def test_endpoint(request):
        return PlainTextResponse("test")
    
    routes = [
        Route("/test", endpoint=test_endpoint, name="test_route"),
    ]
    
    app = Starlette(routes=routes)
    app.state.response_bandwidth_limits = {"test_route": 100}
    middleware = ResponseBandwidthLimiterMiddleware(app)
    
    # モックリクエストを作成
    mock_request = Request(scope={"type": "http", "app": app, "path": "/test", "method": "GET"})
    
    # ルート名でルートを見つけられるか（新しいシグネチャに合わせて更新）
    assert middleware.get_handler_name(mock_request, "/test") == "test_route"
    
    # 存在しないパスに対して
    mock_request = Request(scope={"type": "http", "app": app, "path": "/not-exist", "method": "GET"})
    assert middleware.get_handler_name(mock_request, "/not-exist") is None

# Starletteのネストされたルートテスト
def test_starlette_nested_routes():
    async def api_endpoint(request):
        return PlainTextResponse("API response")
    
    routes = [
        Route("/api/data", endpoint=api_endpoint, name="api_endpoint"),
    ]
    
    app = Starlette(routes=routes)
    app.state.response_bandwidth_limits = {"api_endpoint": 50}
    middleware = ResponseBandwidthLimiterMiddleware(app)
    
    # モックリクエスト
    mock_request = Request(scope={"type": "http", "app": app, "path": "/api/data", "method": "GET"})
    
    # 複雑なパスでも正しくルートを解決できるか
    assert middleware.get_handler_name(mock_request, "/api/data") == "api_endpoint"

# Starletteの動的ルート解決テスト
def test_starlette_dynamic_route_resolution():
    async def item_endpoint(request):
        return PlainTextResponse("item")

    routes = [
        Route("/items/{item_id}", endpoint=item_endpoint, name="item_endpoint"),
    ]

    app = Starlette(routes=routes)
    app.state.response_bandwidth_limits = {"item_endpoint": 100}
    middleware = ResponseBandwidthLimiterMiddleware(app)

    mock_request = Request(scope={"type": "http", "app": app, "path": "/items/123", "method": "GET"})
    assert middleware.get_handler_name(mock_request, "/items/123") == "item_endpoint"

def test_starlette_small_plain_response_is_delayed_before_first_chunk(recorded_sleep_calls):
    async def slow_response(request):
        return PlainTextResponse("x" * 20)

    routes = [
        Route("/slow", endpoint=slow_response, name="slow_response"),
    ]

    app = Starlette(routes=routes)
    app.state.response_bandwidth_limits = {"slow_response": 10}
    app.add_middleware(ResponseBandwidthLimiterMiddleware)

    client = TestClient(app)
    response = client.get("/slow")

    assert response.status_code == 200
    assert response.text == "x" * 20
    assert recorded_sleep_calls == pytest.approx([1.0, 1.0])


def test_starlette_policy_rejects_per_ip():
    limiter = ResponseBandwidthLimiter()

    async def limited(request):
        return PlainTextResponse("ok")

    limited = limiter.limit_rules([Rule(count=1, per="second", action=Reject(detail="starlette limited"))])(limited)

    app = Starlette(routes=[Route("/limited", endpoint=limited)])
    app.state.response_bandwidth_limiter = limiter
    app.add_middleware(ResponseBandwidthLimiterMiddleware)

    client = TestClient(app)

    assert client.get("/limited", headers={"X-Forwarded-For": "10.0.0.10"}).status_code == 200

    rejected = client.get("/limited", headers={"X-Forwarded-For": "10.0.0.10"})
    assert rejected.status_code == 429
    assert rejected.json()["detail"] == "starlette limited"

    assert client.get("/limited", headers={"X-Forwarded-For": "10.0.0.11"}).status_code == 200
