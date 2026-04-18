import pytest
from fastapi import FastAPI, Request
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from fastapi.testclient import TestClient
from response_bandwidth_limiter import (
    set_response_bandwidth_limit,
    endpoint_bandwidth_limits,
    ResponseBandwidthLimiterMiddleware,
)

# デコレータの基本的なテスト
def test_decorator_sets_limit():
    # テスト前に既存の値をクリア
    endpoint_bandwidth_limits.clear()
    
    @set_response_bandwidth_limit(1024)
    async def test_function(request):
        return PlainTextResponse("test")
    
    # デコレータによって正しくグローバル変数に登録されているか
    assert "test_function" in endpoint_bandwidth_limits
    assert endpoint_bandwidth_limits["test_function"] == 1024

# 複数の関数に対するデコレータのテスト
def test_multiple_decorated_functions():
    # テスト前に既存の値をクリア
    endpoint_bandwidth_limits.clear()
    
    @set_response_bandwidth_limit(100)
    async def function1(request):
        return {"message": "function1"}
    
    @set_response_bandwidth_limit(200)
    async def function2(request):
        return {"message": "function2"}
    
    # 複数の関数が正しく登録されているか
    assert "function1" in endpoint_bandwidth_limits
    assert endpoint_bandwidth_limits["function1"] == 100
    assert "function2" in endpoint_bandwidth_limits
    assert endpoint_bandwidth_limits["function2"] == 200

def test_decorator_rejects_non_positive_limit():
    endpoint_bandwidth_limits.clear()

    with pytest.raises(ValueError):
        @set_response_bandwidth_limit(0)
        async def zero_limit(request):
            return PlainTextResponse("test")

    with pytest.raises(ValueError):
        @set_response_bandwidth_limit(-100)
        async def negative_limit(request):
            return PlainTextResponse("test")


def test_decorator_rejects_non_integer_limit():
    endpoint_bandwidth_limits.clear()

    with pytest.raises(TypeError):
        @set_response_bandwidth_limit("100")
        async def string_limit(request):
            return PlainTextResponse("test")

    with pytest.raises(TypeError):
        @set_response_bandwidth_limit(1.5)
        async def float_limit(request):
            return PlainTextResponse("test")

# FastAPIとの統合テスト
def test_fastapi_decorator_integration(recorded_limit_calls):
    # テスト前に既存の値をクリア
    endpoint_bandwidth_limits.clear()
    
    app = FastAPI()
    app.add_middleware(ResponseBandwidthLimiterMiddleware)
    
    @app.get("/slow-endpoint")
    @set_response_bandwidth_limit(100)
    async def slow_endpoint(request: Request):
        return PlainTextResponse("a" * 5000)
    
    @app.get("/fast-endpoint")
    @set_response_bandwidth_limit(5000)
    async def fast_endpoint(request: Request):
        return PlainTextResponse("b" * 5000)
    
    client = TestClient(app)

    slow_response = client.get("/slow-endpoint")
    fast_response = client.get("/fast-endpoint")
    
    # レスポンス内容の検証
    assert slow_response.status_code == 200
    assert len(slow_response.content) == 5000
    assert fast_response.status_code == 200
    assert len(fast_response.content) == 5000

    assert [call["rate"] for call in recorded_limit_calls] == [100, 5000]

# Starletteとの統合テスト
def test_starlette_decorator_integration(recorded_limit_calls):
    # テスト前に既存の値をクリア
    endpoint_bandwidth_limits.clear()
    
    @set_response_bandwidth_limit(100)
    async def slow_endpoint(request):
        return PlainTextResponse("a" * 5000)
    
    @set_response_bandwidth_limit(5000)
    async def fast_endpoint(request):
        return PlainTextResponse("b" * 5000)
    
    routes = [
        Route("/slow", endpoint=slow_endpoint),
        Route("/fast", endpoint=fast_endpoint),
    ]
    
    app = Starlette(routes=routes)
    app.add_middleware(ResponseBandwidthLimiterMiddleware)
    
    client = TestClient(app)

    slow_response = client.get("/slow")
    fast_response = client.get("/fast")
    
    # レスポンス検証
    assert slow_response.status_code == 200
    assert len(slow_response.content) == 5000
    assert fast_response.status_code == 200
    assert len(fast_response.content) == 5000

    assert [call["rate"] for call in recorded_limit_calls] == [100, 5000]
