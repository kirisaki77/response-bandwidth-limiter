import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, StreamingResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from response_bandwidth_limiter import ResponseBandwidthLimiterMiddleware
from starlette.requests import Request
import time

# Starletteのミドルウェアテスト
def test_starlette_middleware():
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

# 帯域制限の実効性テスト（Starlette版）
def test_starlette_bandwidth_limit_effectiveness():
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
    
    # 高速レスポンスの時間計測
    start_time = time.time()
    fast_response = client.get("/fast")
    fast_elapsed = time.time() - start_time
    
    # 低速レスポンスの時間計測
    start_time = time.time()
    slow_response = client.get("/slow")
    slow_elapsed = time.time() - start_time
    
    # レスポンス検証
    assert len(fast_response.content) == 10000
    assert len(slow_response.content) == 10000
    
    # 速度比の検証
    expected_ratio = fast_limit / slow_limit  # 理論上の比率
    actual_ratio = slow_elapsed / fast_elapsed if fast_elapsed > 0 else 1
    
    # テスト環境の不確実性を考慮した緩めの条件
    assert actual_ratio > (expected_ratio * 0.5), f"Starlette速度制限が期待通り機能していません。高速: {fast_elapsed:.2f}秒, 低速: {slow_elapsed:.2f}秒, 比率: {actual_ratio:.2f} (期待: >{expected_ratio * 0.5:.2f})"
    print(f"Starlette帯域制限の効果を確認: 高速({fast_limit}b/s): {fast_elapsed:.2f}秒, 低速({slow_limit}b/s): {slow_elapsed:.2f}秒, 比率: {actual_ratio:.2f}")

# ストリーミングレスポンスでの帯域制限テスト（Starlette版）
def test_starlette_streaming_bandwidth_limit():
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
    
    # 時間計測
    start_time = time.time()
    fast_response = client.get("/fast-stream")
    fast_elapsed = time.time() - start_time
    
    start_time = time.time()
    slow_response = client.get("/slow-stream")
    slow_elapsed = time.time() - start_time
    
    # レスポンスデータの検証
    assert len(fast_response.content) == chunk_size * chunks
    assert len(slow_response.content) == chunk_size * chunks
    
    # 速度比の検証
    expected_ratio = 2000 / 500  # 理論上の比率
    actual_ratio = slow_elapsed / fast_elapsed if fast_elapsed > 0 else 1
    
    # テスト環境を考慮した緩めの条件
    assert actual_ratio > 1.5, f"Starletteストリーミングでの速度制限が期待通り機能していません。比率: {actual_ratio:.2f}"
    print(f"Starletteストリーミング帯域制限の効果: 高速: {fast_elapsed:.2f}秒, 低速: {slow_elapsed:.2f}秒, 比率: {actual_ratio:.2f}")

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
    mock_request = Request(scope={"type": "http", "app": app, "path": "/test"})
    
    # ルート名でルートを見つけられるか（新しいシグネチャに合わせて更新）
    assert middleware.get_handler_name(mock_request, "/test") == "test_route"
    
    # 存在しないパスに対して
    mock_request = Request(scope={"type": "http", "app": app, "path": "/not-exist"})
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
    mock_request = Request(scope={"type": "http", "app": app, "path": "/api/data"})
    
    # 複雑なパスでも正しくルートを解決できるか
    assert middleware.get_handler_name(mock_request, "/api/data") == "api_endpoint"
