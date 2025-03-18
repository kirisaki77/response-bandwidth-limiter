import pytest
from fastapi import FastAPI, Request
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from fastapi.testclient import TestClient
from bandwidth_limiter import (
    BandwidthLimiter,
    BandwidthLimiterMiddleware,
    BandwidthLimitExceeded,
    _bandwidth_limit_exceeded_handler,
)
import time

# FastAPIの統合テスト
def test_fastapi_integration():
    app = FastAPI()
    limiter = BandwidthLimiter()
    
    @app.get("/slow")
    @limiter.limit(50)
    async def slow_endpoint(request: Request):
        return PlainTextResponse("a" * 150)
    
    @app.get("/fast")
    async def fast_endpoint():
        return PlainTextResponse("b" * 150)
    
    # ミドルウェア登録
    app.state.bandwidth_limiter = limiter
    app.add_middleware(BandwidthLimiterMiddleware)
    
    client = TestClient(app)
    
    # 設定が正しく登録されていることを確認
    assert "slow_endpoint" in limiter.routes
    assert limiter.routes["slow_endpoint"] == 50
    
    # レスポンスの検証
    slow_response = client.get("/slow")
    fast_response = client.get("/fast")
    
    # レスポンス内容の検証
    assert slow_response.status_code == 200
    assert len(slow_response.content) == 150
    assert fast_response.status_code == 200
    assert len(fast_response.content) == 150

# Starletteの統合テスト
def test_starlette_integration():
    limiter = BandwidthLimiter()
    
    async def slow_endpoint(request):
        return PlainTextResponse("a" * 200)
    
    async def fast_endpoint(request):
        return PlainTextResponse("b" * 200)
    
    # リミットを適用
    slow_with_limit = limiter.limit(100)(slow_endpoint)
    
    routes = [
        Route("/slow", endpoint=slow_with_limit),
        Route("/fast", endpoint=fast_endpoint),
    ]
    
    app = Starlette(routes=routes)
    limiter.init_app(app)
    
    client = TestClient(app)
    
    # レスポンスの検証
    slow_response = client.get("/slow")
    fast_response = client.get("/fast")
    
    # レスポンス内容の検証
    assert slow_response.status_code == 200
    assert len(slow_response.content) == 200
    assert fast_response.status_code == 200
    assert len(fast_response.content) == 200
    
    # 設定の検証
    assert "slow_endpoint" in limiter.routes
