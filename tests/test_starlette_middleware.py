import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from response_bandwidth_limiter import ResponseBandwidthLimiterMiddleware, StarletteResponseBandwidthLimiterMiddleware
from starlette.requests import Request

# Starletteのミドルウェアテスト
def test_starlette_middleware():
    async def test_endpoint(request):
        return PlainTextResponse("a" * 300)
    
    routes = [
        Route("/test", endpoint=test_endpoint, name="test_route"),
    ]
    
    app = Starlette(routes=routes)
    
    app.add_middleware(
        ResponseBandwidthLimiterMiddleware, 
        limits={"test_route": 100}
    )
    
    client = TestClient(app)
    response = client.get("/test")
    
    assert response.status_code == 200
    assert len(response.content) == 300

# Starletteのルート解決テスト
def test_starlette_route_resolution():
    async def test_endpoint(request):
        return PlainTextResponse("test")
    
    routes = [
        Route("/test", endpoint=test_endpoint, name="test_route"),
    ]
    
    app = Starlette(routes=routes)
    middleware = StarletteResponseBandwidthLimiterMiddleware(app, limits={"test_route": 100})
    
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
    middleware = ResponseBandwidthLimiterMiddleware(app, limits={"api_endpoint": 50})
    
    # モックリクエスト
    mock_request = Request(scope={"type": "http", "app": app, "path": "/api/data"})
    
    # 複雑なパスでも正しくルートを解決できるか
    assert middleware.get_handler_name(mock_request, "/api/data") == "api_endpoint"
