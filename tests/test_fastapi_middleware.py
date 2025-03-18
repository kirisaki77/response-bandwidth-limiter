import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse
from response_bandwidth_limiter import ResponseBandwidthLimiterMiddleware, FastAPIResponseBandwidthLimiterMiddleware
import time

# FastAPIのミドルウェアテスト
def test_fastapi_middleware():
    app = FastAPI()
    
    # 帯域制限を追加 (100 bytes/sec)
    app.add_middleware(
        ResponseBandwidthLimiterMiddleware, 
        limits={"read_test": 100}
    )
    
    @app.get("/test")
    async def read_test():
        # 300バイトのレスポンス
        return PlainTextResponse("a" * 300)
    
    client = TestClient(app)
    response = client.get("/test")
    
    # ベーシックな検証
    assert response.status_code == 200
    assert len(response.content) == 300

# FastAPI専用ミドルウェアのテスト
def test_fastapi_specific_middleware():
    app = FastAPI()
    
    # FastAPI専用ミドルウェアを使用
    app.add_middleware(
        FastAPIResponseBandwidthLimiterMiddleware,
        limits={"read_test": 100}
    )
    
    @app.get("/test")
    async def read_test():
        return PlainTextResponse("a" * 300)
    
    client = TestClient(app)
    response = client.get("/test")
    
    assert response.status_code == 200
    assert len(response.content) == 300

# FastAPIのルート解決テスト
def test_fastapi_route_resolution():
    app = FastAPI()
    middleware = ResponseBandwidthLimiterMiddleware(app, limits={"custom_name": 100})
    
    # モックリクエストを作成
    mock_request = Request(scope={"type": "http", "app": app, "path": "/not-exist"})
    
    # 実装ロジックのテスト - 新しい引数で呼び出し
    assert middleware.get_handler_name(mock_request, "/not-exist") is None
    
    @app.get("/test", name="custom_name")
    async def read_test():
        return {"hello": "world"}
    
    # カスタム名でルートを見つけられるか
    mock_request = Request(scope={"type": "http", "app": app, "path": "/test"})
    assert middleware.get_handler_name(mock_request, "/test") == "custom_name"
    
    # 関数名でルートを見つけられるか
    middleware = ResponseBandwidthLimiterMiddleware(app, limits={"read_test": 200})
    assert middleware.get_handler_name(mock_request, "/test") == "read_test"
