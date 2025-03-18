import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse
from bandwidth_limiter import BandwidthLimiterMiddleware
import time
import asyncio

# FastAPIのミドルウェアテスト
def test_middleware_with_fastapi():
    app = FastAPI()
    
    # 帯域制限を追加 (100 bytes/sec)
    app.add_middleware(
        BandwidthLimiterMiddleware, 
        limits={"read_test": 100}
    )
    
    @app.get("/test")
    async def read_test():
        # 300バイトのレスポンス
        return PlainTextResponse("a" * 300)
    
    client = TestClient(app)
    
    # レスポンス時間を測定
    start_time = time.time()
    response = client.get("/test")
    end_time = time.time()
    
    # 300バイトを100バイト/秒で送信するには約3秒かかる
    # テストでは多少の余裕を持たせる
    assert response.status_code == 200
    assert len(response.content) == 300
    assert (end_time - start_time) >= 2.5  # ほぼ3秒かかるはず

# オーバーライドされたアプリケーションルート
def test_get_handler_name():
    app = FastAPI()
    middleware = BandwidthLimiterMiddleware(app, limits={"custom_name": 100})
    
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
