import pytest
from fastapi import FastAPI, Request
from starlette.responses import PlainTextResponse, StreamingResponse
from fastapi.testclient import TestClient
from response_bandwidth_limiter import (
    ResponseBandwidthLimiter,
    ResponseBandwidthLimiterMiddleware,
    ResponseBandwidthLimitExceeded,
    _response_bandwidth_limit_exceeded_handler,
)
import time
import asyncio

# FastAPIの基本統合テスト
def test_fastapi_basic_integration():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    
    @app.get("/slow")
    @limiter.limit(50)
    async def slow_endpoint(request: Request):
        return PlainTextResponse("a" * 150)
    
    @app.get("/fast")
    async def fast_endpoint():
        return PlainTextResponse("b" * 150)
    
    # ミドルウェア登録
    app.state.response_bandwidth_limiter = limiter
    app.add_middleware(ResponseBandwidthLimiterMiddleware)
    
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

# FastAPIのストリーミングレスポンステスト
def test_fastapi_streaming_response():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    app.state.response_bandwidth_limiter = limiter
    
    async def number_generator():
        for i in range(5):
            yield f"data_packet{i}\n".encode("utf-8")
    
    @app.get("/stream")
    @limiter.limit(100)
    async def stream_endpoint(request: Request):
        return StreamingResponse(number_generator())
    
    app.add_middleware(ResponseBandwidthLimiterMiddleware)
    
    client = TestClient(app)
    response = client.get("/stream")
    
    assert response.status_code == 200
    content = response.content
    assert "data_packet0".encode("utf-8") in content
    assert "data_packet4".encode("utf-8") in content

# ミドルウェア直接使用テストを削除
