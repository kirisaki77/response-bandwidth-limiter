import pytest
from fastapi import FastAPI, Request
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, JSONResponse
from starlette.routing import Route
from fastapi.testclient import TestClient
from starlette.testclient import TestClient as StarletteTestClient
from response_bandwidth_limiter import ResponseBandwidthLimiter, ResponseBandwidthLimiterMiddleware


# FastAPIでの動的帯域制限テスト
def test_fastapi_dynamic_bandwidth_limit(recorded_limit_calls):
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    app.state.response_bandwidth_limiter = limiter
    app.add_middleware(ResponseBandwidthLimiterMiddleware)
    
    data_size = 10000  # 10KBのデータ
    initial_limit = 5000  # 初期制限: 5000 bytes/sec
    new_limit = 500      # 変更後制限: 500 bytes/sec
    
    # テスト用エンドポイント (初期制限: 5000 bytes/sec)
    @app.get("/data")
    async def get_data():
        return PlainTextResponse("a" * data_size)
        
    # 制限変更用エンドポイント
    @app.get("/admin/set-limit")
    async def set_limit(endpoint: str, limit: int):
        limiter.routes[endpoint] = limit
        return {"status": "success", "endpoint": endpoint, "limit": limit}
    
    # テスト用クライアント
    client = TestClient(app)
    
    # エンドポイント名を設定
    limiter.routes["get_data"] = initial_limit
    
    response = client.get("/data")
    
    # レスポンス内容の検証
    assert response.status_code == 200
    assert len(response.content) == data_size
    assert [call["rate"] for call in recorded_limit_calls] == [initial_limit]
    
    # 制限値を動的に変更
    client.get(f"/admin/set-limit?endpoint=get_data&limit={new_limit}")
    
    # 制限値が変更されたことを確認
    assert limiter.routes["get_data"] == new_limit

    recorded_limit_calls.clear()
    response = client.get("/data")
    
    # レスポンス内容の検証
    assert response.status_code == 200
    assert len(response.content) == data_size

    assert [call["rate"] for call in recorded_limit_calls] == [new_limit]


# Starletteでの動的帯域制限テスト
def test_starlette_dynamic_bandwidth_limit(recorded_limit_calls):
    limiter = ResponseBandwidthLimiter()
    
    data_size = 10000  # 10KBのデータ
    
    # テスト用エンドポイント
    async def get_data(request):
        return PlainTextResponse("a" * data_size)
    
    # 制限変更用エンドポイント
    async def set_limit(request):
        endpoint = request.query_params.get("endpoint")
        limit = int(request.query_params.get("limit"))
        limiter.routes[endpoint] = limit
        # 修正: 適切なJSONResponseを返す
        return JSONResponse({
            "success": True, 
            "endpoint": endpoint, 
            "limit": limit
        })
    
    # ルートを定義
    routes = [
        Route("/data", endpoint=get_data),
        Route("/admin/set-limit", endpoint=set_limit)
    ]
    
    app = Starlette(routes=routes)
    app.state.response_bandwidth_limiter = limiter
    app.add_middleware(ResponseBandwidthLimiterMiddleware)
    
    # テスト用クライアント
    client = StarletteTestClient(app)
    
    # 初期制限値と変更後の制限値
    initial_limit = 5000  # 5000 bytes/sec
    new_limit = 500       # 500 bytes/sec
    
    # エンドポイント名を設定
    limiter.routes["get_data"] = initial_limit
    
    response = client.get("/data")
    
    # レスポンス内容の検証
    assert response.status_code == 200
    assert len(response.content) == data_size
    assert [call["rate"] for call in recorded_limit_calls] == [initial_limit]
    
    # 制限値を動的に変更
    client.get(f"/admin/set-limit?endpoint=get_data&limit={new_limit}")
    
    # 制限値が変更されたことを確認
    assert limiter.routes["get_data"] == new_limit

    recorded_limit_calls.clear()
    response = client.get("/data")
    
    # レスポンス内容の検証
    assert response.status_code == 200
    assert len(response.content) == data_size

    assert [call["rate"] for call in recorded_limit_calls] == [new_limit]


# ストリーミングレスポンスでの動的帯域制限テスト
def test_streaming_dynamic_bandwidth_limit(recorded_limit_calls):
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    app.state.response_bandwidth_limiter = limiter
    app.add_middleware(ResponseBandwidthLimiterMiddleware)
    
    chunk_size = 1000  # 各チャンクのサイズ
    chunks = 5        # チャンク数
    
    # ストリーミングレスポンスを返すジェネレータ
    async def number_generator():
        for i in range(chunks):
            yield f"{'x' * chunk_size}".encode("utf-8")
    
    # ストリーミングレスポンス用エンドポイント
    @app.get("/stream")
    async def stream_data():
        from starlette.responses import StreamingResponse
        return StreamingResponse(number_generator())
    
    # 制限変更用エンドポイント
    @app.get("/admin/set-stream-limit")
    async def set_stream_limit(limit: int):
        limiter.routes["stream_data"] = limit
        return {"status": "success", "endpoint": "stream_data", "limit": limit}
    
    client = TestClient(app)
    
    # 初期制限値と変更後の制限値
    initial_limit = 2000  # 2000 bytes/sec
    new_limit = 500       # 500 bytes/sec
    
    # エンドポイント名を設定
    limiter.routes["stream_data"] = initial_limit
    
    response = client.get("/stream")
    
    # レスポンス内容の検証
    assert response.status_code == 200
    assert len(response.content) == chunk_size * chunks
    assert [call["rate"] for call in recorded_limit_calls] == [initial_limit] * chunks
    
    # 制限値を動的に変更
    client.get(f"/admin/set-stream-limit?limit={new_limit}")
    
    # 制限値が変更されたことを確認
    assert limiter.routes["stream_data"] == new_limit

    recorded_limit_calls.clear()
    response = client.get("/stream")
    
    # レスポンス内容の検証
    assert response.status_code == 200
    assert len(response.content) == chunk_size * chunks

    assert [call["rate"] for call in recorded_limit_calls] == [new_limit] * chunks
