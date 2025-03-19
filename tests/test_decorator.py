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
import time

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

# FastAPIとの統合テスト
def test_fastapi_decorator_integration():
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
    
    # 速度測定とレスポンス検証
    start_time = time.time()
    slow_response = client.get("/slow-endpoint")
    slow_elapsed = time.time() - start_time
    
    start_time = time.time()
    fast_response = client.get("/fast-endpoint")
    fast_elapsed = time.time() - start_time
    
    # レスポンス内容の検証
    assert slow_response.status_code == 200
    assert len(slow_response.content) == 5000
    assert fast_response.status_code == 200
    assert len(fast_response.content) == 5000
    
    # 速度比の検証（厳密なタイミングは環境依存なので緩めの条件で）
    expected_ratio = 5000 / 100  # 理論上の比率
    actual_ratio = slow_elapsed / fast_elapsed if fast_elapsed > 0 else 1
    
    # テスト環境の不確実性を考慮した緩めの条件
    assert actual_ratio > 1.5, f"速度制限の差が期待より小さい。高速: {fast_elapsed:.2f}秒, 低速: {slow_elapsed:.2f}秒, 比率: {actual_ratio:.2f}"
    print(f"デコレータ帯域制限の効果: 高速(5000b/s): {fast_elapsed:.2f}秒, 低速(100b/s): {slow_elapsed:.2f}秒, 比率: {actual_ratio:.2f}")

# Starletteとの統合テスト
def test_starlette_decorator_integration():
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
    
    # 速度測定とレスポンス検証
    start_time = time.time()
    slow_response = client.get("/slow")
    slow_elapsed = time.time() - start_time
    
    start_time = time.time()
    fast_response = client.get("/fast")
    fast_elapsed = time.time() - start_time
    
    # レスポンス検証
    assert slow_response.status_code == 200
    assert len(slow_response.content) == 5000
    assert fast_response.status_code == 200
    assert len(fast_response.content) == 5000
    
    # 速度比の検証
    expected_ratio = 5000 / 100  # 理論上の比率
    actual_ratio = slow_elapsed / fast_elapsed if fast_elapsed > 0 else 1
    
    # テスト環境の不確実性を考慮した緩めの条件
    assert actual_ratio > 1.5, f"Starletteでの速度制限の差が期待より小さい。高速: {fast_elapsed:.2f}秒, 低速: {slow_elapsed:.2f}秒, 比率: {actual_ratio:.2f}"
    print(f"Starletteデコレータ帯域制限の効果: 高速(5000b/s): {fast_elapsed:.2f}秒, 低速(100b/s): {slow_elapsed:.2f}秒, 比率: {actual_ratio:.2f}")
