import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse
from starlette.responses import PlainTextResponse
from response_bandwidth_limiter import Delay, Reject, ResponseBandwidthLimiter, ResponseBandwidthLimitExceeded, Rule, Throttle, _response_bandwidth_limit_exceeded_handler, get_endpoint_name, get_route_path

# デコレータAPIのテスト
def test_limiter_decorator():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    app.state.response_bandwidth_limiter = limiter
    app.add_exception_handler(ResponseBandwidthLimitExceeded, _response_bandwidth_limit_exceeded_handler)
    
    # 帯域制限付きエンドポイント (200 bytes/sec)
    @app.get("/test")
    @limiter.limit(200)
    async def read_test(request: Request):
        return PlainTextResponse("a" * 600)
    
    client = TestClient(app)
    
    # ルート登録の検証
    assert "read_test" in limiter.routes
    assert limiter.routes["read_test"] == 200
    
    # レスポンス内容の検証
    response = client.get("/test")
    assert response.status_code == 200
    assert len(response.content) == 600
    
    # 注: テスト環境ではasyncioのsleepが適切に機能しないため
    # 時間計測による検証はスキップします

# 不正な引数のテスト
def test_invalid_limit_argument():
    limiter = ResponseBandwidthLimiter()
    
    # 文字列を渡すと例外が発生する
    with pytest.raises(TypeError):
        @limiter.limit("not_a_number")
        async def invalid_test(request: Request):
            pass

    with pytest.raises(ValueError):
        @limiter.limit(0)
        async def zero_limit_test(request: Request):
            pass

    with pytest.raises(ValueError):
        @limiter.limit(-1)
        async def negative_limit_test(request: Request):
            pass
    
    # 正しく動作する整数の場合
    @limiter.limit(1000)
    async def valid_test(request: Request):
        pass
    
    # ルート名が正しく保存されているか
    assert "valid_test" in limiter.routes
    assert limiter.routes["valid_test"] == 1000


def test_limit_rules_registers_policy():
    limiter = ResponseBandwidthLimiter()
    rules = [Rule(count=2, per="second", action=Reject())]

    @limiter.limit_rules(rules)
    async def limited_endpoint(request: Request):
        return PlainTextResponse("ok")

    assert limiter.policies["limited_endpoint"] == rules


def test_update_route_and_policy_manage_runtime_configuration():
    limiter = ResponseBandwidthLimiter()
    rules = [Rule(count=1, per="second", action=Reject())]

    limiter.update_route("download", 128)
    limiter.update_policy("download", rules)

    assert limiter.get_limit("download") == 128
    assert limiter.get_rules("download") == rules

    limiter.remove_route("download")
    limiter.remove_policy("download")

    assert limiter.get_limit("download") is None
    assert limiter.get_rules("download") == []


def test_invalid_limit_rules_argument():
    limiter = ResponseBandwidthLimiter()

    with pytest.raises(TypeError):
        limiter.limit_rules("not_a_list")

    with pytest.raises(ValueError):
        limiter.limit_rules([])

    with pytest.raises(TypeError):
        limiter.limit_rules(["not_a_rule"])


def test_rule_and_action_validation():
    with pytest.raises(ValueError):
        Rule(count=0, per="second", action=Reject())

    with pytest.raises(ValueError):
        Rule(count=1, per="day", action=Reject())

    with pytest.raises(ValueError):
        Throttle(bytes_per_sec=0)

    with pytest.raises(ValueError):
        Delay(seconds=0)


def test_rule_and_action_validation_rejects_invalid_types_and_scope():
    with pytest.raises(TypeError):
        Throttle(bytes_per_sec="fast")

    with pytest.raises(TypeError):
        Reject(status_code="429")

    with pytest.raises(ValueError):
        Reject(status_code=399)

    with pytest.raises(TypeError):
        Reject(detail=123)

    with pytest.raises(ValueError):
        Rule(count=1, per="second", action=Reject(), scope="global")

    with pytest.raises(TypeError):
        Rule(count=1, per="second", action="invalid")


def test_rule_window_seconds_supports_all_periods():
    assert Rule(count=1, per="second", action=Reject()).window_seconds == 1
    assert Rule(count=1, per="minute", action=Reject()).window_seconds == 60
    assert Rule(count=1, per="hour", action=Reject()).window_seconds == 3600


def test_action_priority_values_are_stable():
    assert Reject().priority == 0
    assert Delay(seconds=0.1).priority == 1
    assert Throttle(bytes_per_sec=1).priority == 2


def test_response_bandwidth_limit_exceeded_exposes_message():
    exc = ResponseBandwidthLimitExceeded(limit=128, endpoint="download")

    assert exc.limit == 128
    assert exc.endpoint == "download"
    assert exc.message == "Endpoint download is limited to 128 bytes/second"
    assert str(exc) == exc.message


@pytest.mark.asyncio
async def test_response_bandwidth_limit_exceeded_handler_returns_json_response():
    app = FastAPI()
    request = Request(scope={"type": "http", "app": app, "path": "/download", "method": "GET", "headers": []})
    exc = ResponseBandwidthLimitExceeded(limit=256, endpoint="download")

    response = await _response_bandwidth_limit_exceeded_handler(request, exc)

    assert isinstance(response, JSONResponse)
    assert response.status_code == 429
    assert response.body == b'{"error":"Bandwidth Limit Exceeded","detail":"Endpoint download is limited to 256 bytes/second"}'


def test_util_functions_return_endpoint_and_route_path():
    scope = {
        "type": "http",
        "path": "/items/123",
        "endpoint": "handler_name",
        "method": "GET",
    }
    request = Request(scope=scope)

    assert get_endpoint_name(request) == "handler_name"
    assert get_route_path(request) == "/items/123"


def test_get_endpoint_name_falls_back_to_path():
    request = Request(scope={"type": "http", "path": "/fallback", "method": "GET"})

    assert get_endpoint_name(request) == "/fallback"


def test_init_app_exposes_policies_and_routes():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    limiter.update_route("download", 128)
    limiter.update_policy("download", [Rule(count=1, per="second", action=Reject())])

    limiter.init_app(app)

    assert app.state.response_bandwidth_limiter is limiter
    assert limiter.get_limit("download") == 128
    assert limiter.get_rules("download")[0].count == 1
