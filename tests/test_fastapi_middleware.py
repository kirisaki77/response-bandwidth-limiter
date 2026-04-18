import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse, StreamingResponse
from response_bandwidth_limiter import Delay, Reject, ResponseBandwidthLimiter, ResponseBandwidthLimiterMiddleware, Rule, Throttle
import time
import asyncio

# FastAPIのミドルウェアテスト
def test_fastapi_middleware():
    app = FastAPI()
    
    # 帯域制限を追加 (100 bytes/sec)
    app.state.response_bandwidth_limits = {"read_test": 100}
    app.add_middleware(ResponseBandwidthLimiterMiddleware)
    
    @app.get("/test")
    async def read_test():
        # 300バイトのレスポンス
        return PlainTextResponse("a" * 300)
    
    client = TestClient(app)
    response = client.get("/test")
    
    # ベーシックな検証
    assert response.status_code == 200
    assert len(response.content) == 300

# 帯域制限の実効性テスト
def test_bandwidth_limit_effectiveness():
    app = FastAPI()
    
    # 大きめの帯域制限を設定 (5000 bytes/sec)
    fast_limit = 5000
    slow_limit = 500
    
    # 新しい方法で制限を設定
    app.state.response_bandwidth_limits = {
        "fast_response": fast_limit,
        "slow_response": slow_limit
    }
    
    app.add_middleware(ResponseBandwidthLimiterMiddleware)
    
    data_size = 10000  # 10KBのデータ
    
    @app.get("/fast")
    async def fast_response():
        return PlainTextResponse("a" * data_size)
    
    @app.get("/slow")
    async def slow_response():
        return PlainTextResponse("b" * data_size)
    
    client = TestClient(app)
    
    # 高速レスポンスの時間計測
    start_time = time.time()
    fast_response = client.get("/fast")
    fast_elapsed = time.time() - start_time
    
    # 低速レスポンスの時間計測
    start_time = time.time()
    slow_response = client.get("/slow")
    slow_elapsed = time.time() - start_time
    
    # 両方のレスポンスが完全に受信されていることを確認
    assert len(fast_response.content) == data_size
    assert len(slow_response.content) == data_size
    
    # 低速レスポンスは高速レスポンスより時間がかかるはず
    # 正確な時間は保証できないが、オーダーの差があるはず
    expected_ratio = fast_limit / slow_limit  # 理論上の比率
    actual_ratio = slow_elapsed / fast_elapsed if fast_elapsed > 0 else 1
    
    # テスト環境の不確実性を考慮して、緩めの条件で検証
    # 少なくとも制限の差の半分程度は反映されているべき
    assert actual_ratio > (expected_ratio * 0.5), f"速度制限が期待通り機能していません。高速: {fast_elapsed:.2f}秒, 低速: {slow_elapsed:.2f}秒, 比率: {actual_ratio:.2f} (期待: >{expected_ratio * 0.5:.2f})"
    print(f"帯域制限の効果を確認: 高速({fast_limit}b/s): {fast_elapsed:.2f}秒, 低速({slow_limit}b/s): {slow_elapsed:.2f}秒, 比率: {actual_ratio:.2f}")

# ストリーミングレスポンスでの帯域制限テスト
def test_streaming_bandwidth_limit():
    app = FastAPI()
    
    # 異なる帯域制限を設定
    app.state.response_bandwidth_limits = {
        "fast_stream": 2000, 
        "slow_stream": 500
    }
    
    app.add_middleware(ResponseBandwidthLimiterMiddleware)
    
    chunk_size = 1000  # 各チャンクのサイズ
    chunks = 5  # チャンク数
    
    async def fast_generator():
        for i in range(chunks):
            yield f"{'a' * chunk_size}".encode("utf-8")
    
    async def slow_generator():
        for i in range(chunks):
            yield f"{'b' * chunk_size}".encode("utf-8")
    
    @app.get("/fast-stream")
    async def fast_stream():
        return StreamingResponse(fast_generator())
    
    @app.get("/slow-stream")
    async def slow_stream():
        return StreamingResponse(slow_generator())
    
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
    assert actual_ratio > 1.5, f"ストリーミングでの速度制限が期待通り機能していません。比率: {actual_ratio:.2f}"
    print(f"ストリーミング帯域制限の効果: 高速: {fast_elapsed:.2f}秒, 低速: {slow_elapsed:.2f}秒, 比率: {actual_ratio:.2f}")

# FastAPIのルート解決テスト
def test_fastapi_route_resolution():
    app = FastAPI()
    app.state.response_bandwidth_limits = {"custom_name": 100}
    middleware = ResponseBandwidthLimiterMiddleware(app)
    
    # モックリクエストを作成
    mock_request = Request(scope={"type": "http", "app": app, "path": "/not-exist", "method": "GET"})
    
    # 実装ロジックのテスト - 新しい引数で呼び出し
    assert middleware.get_handler_name(mock_request, "/not-exist") is None
    
    @app.get("/test", name="custom_name")
    async def read_test():
        return {"hello": "world"}
    
    # カスタム名でルートを見つけられるか
    mock_request = Request(scope={"type": "http", "app": app, "path": "/test", "method": "GET"})
    assert middleware.get_handler_name(mock_request, "/test") == "custom_name"
    
    # 関数名でルートを見つけられるか
    app.state.response_bandwidth_limits = {"read_test": 200}
    middleware = ResponseBandwidthLimiterMiddleware(app)
    assert middleware.get_handler_name(mock_request, "/test") == "read_test"

# FastAPIの動的ルート解決テスト
def test_fastapi_dynamic_route_resolution():
    app = FastAPI()
    app.state.response_bandwidth_limits = {"read_item": 100}

    @app.get("/items/{item_id}")
    async def read_item(item_id: str):
        return {"item_id": item_id}

    middleware = ResponseBandwidthLimiterMiddleware(app)
    mock_request = Request(scope={"type": "http", "app": app, "path": "/items/123", "method": "GET"})
    assert middleware.get_handler_name(mock_request, "/items/123") == "read_item"

# 非ストリーミングレスポンスのヘッダ保持テスト
def test_plain_response_headers_are_preserved():
    app = FastAPI()
    app.state.response_bandwidth_limits = {"read_test": 200}
    app.add_middleware(ResponseBandwidthLimiterMiddleware)

    @app.get("/test")
    async def read_test():
        response = PlainTextResponse("payload", headers={"X-Test": "ok"})
        response.set_cookie("session", "value")
        return response

    client = TestClient(app)
    response = client.get("/test")

    assert response.status_code == 200
    assert response.headers["X-Test"] == "ok"
    assert response.cookies.get("session") == "value"

def test_small_plain_response_is_delayed_before_first_chunk():
    app = FastAPI()
    app.state.response_bandwidth_limits = {"slow_response": 10}
    app.add_middleware(ResponseBandwidthLimiterMiddleware)

    @app.get("/slow")
    async def slow_response():
        return PlainTextResponse("x" * 20)

    client = TestClient(app)

    start_time = time.time()
    response = client.get("/slow")
    elapsed = time.time() - start_time

    assert response.status_code == 200
    assert response.text == "x" * 20
    assert elapsed >= 1.5, f"小さいレスポンスでも帯域制限前に即時送信されています: {elapsed:.2f}秒"


def test_policy_rejects_after_threshold_per_ip():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    app.state.response_bandwidth_limiter = limiter
    app.add_middleware(ResponseBandwidthLimiterMiddleware)

    @app.get("/limited")
    @limiter.limit_rules([Rule(count=2, per="second", action=Reject(detail="too many requests"))])
    async def limited(request: Request):
        return PlainTextResponse("ok")

    client = TestClient(app)

    assert client.get("/limited").status_code == 200
    assert client.get("/limited").status_code == 200

    rejected = client.get("/limited")
    assert rejected.status_code == 429
    assert rejected.json()["detail"] == "too many requests"
    assert rejected.headers["Retry-After"] == "1"


def test_policy_uses_ip_scope_independently():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    app.state.response_bandwidth_limiter = limiter
    app.add_middleware(ResponseBandwidthLimiterMiddleware)

    @app.get("/ip-limited")
    @limiter.limit_rules([Rule(count=1, per="second", action=Reject())])
    async def ip_limited(request: Request):
        return PlainTextResponse("ok")

    client = TestClient(app)

    assert client.get("/ip-limited", headers={"X-Forwarded-For": "10.0.0.1"}).status_code == 200
    assert client.get("/ip-limited", headers={"X-Forwarded-For": "10.0.0.1"}).status_code == 429
    assert client.get("/ip-limited", headers={"X-Forwarded-For": "10.0.0.2"}).status_code == 200


def test_policy_applies_delay_before_handler_execution():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    app.state.response_bandwidth_limiter = limiter
    app.add_middleware(ResponseBandwidthLimiterMiddleware)

    @app.get("/delay")
    @limiter.limit_rules([Rule(count=1, per="second", action=Delay(seconds=0.2))])
    async def delayed(request: Request):
        return PlainTextResponse("ok")

    client = TestClient(app)

    assert client.get("/delay").status_code == 200

    start_time = time.time()
    response = client.get("/delay")
    elapsed = time.time() - start_time

    assert response.status_code == 200
    assert elapsed >= 0.18, f"delay action が適用されていません: {elapsed:.2f}秒"


def test_policy_throttle_overrides_response_rate_after_threshold():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    app.state.response_bandwidth_limiter = limiter
    app.add_middleware(ResponseBandwidthLimiterMiddleware)

    @app.get("/policy-throttle")
    @limiter.limit_rules([Rule(count=1, per="second", action=Throttle(bytes_per_sec=10))])
    async def policy_throttle(request: Request):
        return PlainTextResponse("x" * 20)

    client = TestClient(app)

    first = client.get("/policy-throttle")
    assert first.status_code == 200

    start_time = time.time()
    second = client.get("/policy-throttle")
    elapsed = time.time() - start_time

    assert second.status_code == 200
    assert second.text == "x" * 20
    assert elapsed >= 1.5, f"policy throttle が適用されていません: {elapsed:.2f}秒"


def test_direct_routes_assignment_still_works_with_policy_support():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    app.state.response_bandwidth_limiter = limiter
    app.add_middleware(ResponseBandwidthLimiterMiddleware)

    @app.get("/legacy")
    async def legacy():
        return PlainTextResponse("x" * 20)

    limiter.routes["legacy"] = 10

    start_time = time.time()
    response = TestClient(app).get("/legacy")
    elapsed = time.time() - start_time

    assert response.status_code == 200
    assert elapsed >= 1.5, f"legacy routes 設定が壊れています: {elapsed:.2f}秒"
