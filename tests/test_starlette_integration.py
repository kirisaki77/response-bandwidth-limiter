import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, StreamingResponse
from starlette.routing import Route, Mount
from starlette.testclient import TestClient
from response_bandwidth_limiter import (
    ResponseBandwidthLimiter,
    ResponseBandwidthLimiterMiddleware,
)
import time
import asyncio

# Starletteの基本統合テスト
def test_starlette_basic_integration():
    limiter = ResponseBandwidthLimiter()
    
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

# Starletteのストリーミングレスポンステスト
def test_starlette_streaming_response():
    limiter = ResponseBandwidthLimiter()
    
    async def number_generator():
        for i in range(5):
            yield f"data_packet{i}\n".encode("utf-8")
    
    async def stream_endpoint(request):
        return StreamingResponse(number_generator())
    
    # リミットを適用
    stream_with_limit = limiter.limit(100)(stream_endpoint)
    
    routes = [
        Route("/stream", endpoint=stream_with_limit),
    ]
    
    app = Starlette(routes=routes)
    limiter.init_app(app)
    
    client = TestClient(app)
    response = client.get("/stream")
    
    assert response.status_code == 200
    content = response.content
    assert "data_packet0".encode("utf-8") in content
    assert "data_packet4".encode("utf-8") in content
