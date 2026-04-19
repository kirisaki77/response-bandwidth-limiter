from datetime import timedelta

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse
from response_bandwidth_limiter import ActionProtocol, Delay, PolicyDecision, Reject, ResponseBandwidthLimiter, Rule, SlidingWindowResult, Storage, Throttle, get_endpoint_name, get_route_path
from response_bandwidth_limiter.storage import InMemoryStorage
from response_bandwidth_limiter.policy import PolicyEvaluator

# デコレータAPIのテスト
def test_limiter_decorator():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    app.state.response_bandwidth_limiter = limiter
    
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


def test_configured_names_returns_union_of_routes_and_policies():
    limiter = ResponseBandwidthLimiter()
    limiter.update_route("download", 128)
    limiter.update_policy("upload", [Rule(count=1, per="second", action=Reject())])

    assert limiter.configured_names == {"download", "upload"}

    limiter.update_policy("download", [Rule(count=1, per="second", action=Reject())])
    assert limiter.configured_names == {"download", "upload"}


def test_update_route_rejects_empty_endpoint_name():
    limiter = ResponseBandwidthLimiter()

    with pytest.raises(ValueError):
        limiter.update_route("", 128)


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
        Rule(count=1, per=timedelta(), action=Reject())

    with pytest.raises(ValueError):
        Rule(count=1, per=timedelta(milliseconds=500), action=Reject())

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

    with pytest.raises(ValueError):
        Reject(status_code=600)

    with pytest.raises(TypeError):
        Reject(detail=123)

    with pytest.raises(ValueError):
        Rule(count=1, per="second", action=Reject(), scope="global")

    with pytest.raises(TypeError):
        Rule(count=1, per=123, action=Reject())

    with pytest.raises(TypeError):
        Rule(count=1, per="second", action="invalid")


def test_rule_window_seconds_supports_all_periods():
    assert Rule(count=1, per="second", action=Reject()).window_seconds == 1
    assert Rule(count=1, per="minute", action=Reject()).window_seconds == 60
    assert Rule(count=1, per="hour", action=Reject()).window_seconds == 3600


def test_rule_window_seconds_supports_timedelta_periods():
    assert Rule(count=1, per=timedelta(seconds=1), action=Reject()).window_seconds == 1
    assert Rule(count=1, per=timedelta(minutes=30), action=Reject()).window_seconds == 1800
    assert Rule(count=1, per=timedelta(hours=1), action=Reject()).window_seconds == 3600


def test_action_priority_values_are_stable():
    assert Reject().priority == 0
    assert Delay(seconds=0.1).priority == 1
    assert Throttle(bytes_per_sec=1).priority == 2


def test_actions_expose_decision_metadata():
    assert Throttle(bytes_per_sec=8).decide(3) == PolicyDecision(retry_after=3, throttle_rate=8)
    assert Delay(seconds=0.5).decide(2) == PolicyDecision(retry_after=2, pre_delay=0.5)
    assert Reject(detail="limited").decide(4) == PolicyDecision(
        reject=True,
        reject_status=429,
        reject_detail="limited",
        retry_after=4,
    )


def test_rule_accepts_custom_action_protocol():
    class CustomAction:
        @property
        def priority(self) -> int:
            return 5

        @property
        def sort_key(self) -> int:
            return 1

        def to_dict(self) -> dict[str, str]:
            return {"type": "custom"}

        def decide(self, retry_after: int) -> PolicyDecision:
            return PolicyDecision(retry_after=retry_after)

    action = CustomAction()

    assert isinstance(action, ActionProtocol)
    assert Rule(count=1, per="second", action=action).action is action


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


def test_get_endpoint_name_uses_callable_name_when_available():
    async def endpoint(request: Request):
        return PlainTextResponse("ok")

    request = Request(scope={"type": "http", "path": "/callable", "endpoint": endpoint, "method": "GET"})

    assert get_endpoint_name(request) == "endpoint"


def test_init_app_exposes_policies_and_routes():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    limiter.update_route("download", 128)
    limiter.update_policy("download", [Rule(count=1, per="second", action=Reject())])

    limiter.init_app(app)

    assert app.state.response_bandwidth_limiter is limiter
    assert limiter.get_limit("download") == 128
    assert limiter.get_rules("download")[0].count == 1


@pytest.mark.asyncio
async def test_policy_evaluator_limits_counter_growth():
    evaluator = PolicyEvaluator(time_provider=lambda: 1.0, max_counters=2)
    rule = Rule(count=1, per="second", action=Reject())

    await evaluator.evaluate("client-a", "download", [rule])
    await evaluator.evaluate("client-b", "download", [rule])
    await evaluator.evaluate("client-c", "download", [rule])

    assert len(evaluator.request_counters) == 2


def test_action_to_dict_serialization():
    assert Throttle(bytes_per_sec=100).to_dict() == {"type": "throttle", "bytes_per_sec": 100}
    assert Delay(seconds=0.5).to_dict() == {"type": "delay", "seconds": 0.5}
    assert Reject(status_code=503, detail="overloaded").to_dict() == {
        "type": "reject",
        "status_code": 503,
        "detail": "overloaded",
    }


def test_delay_sort_key_prefers_longer_delays():
    short = Delay(seconds=0.1)
    long = Delay(seconds=1.0)
    assert long.sort_key < short.sort_key


def test_throttle_sort_key_prefers_lower_rate():
    slow = Throttle(bytes_per_sec=10)
    fast = Throttle(bytes_per_sec=1000)
    assert slow.sort_key < fast.sort_key


def test_response_streamer_rejects_invalid_chunk_size():
    from response_bandwidth_limiter.streaming import ResponseStreamer

    with pytest.raises(ValueError):
        ResponseStreamer(chunk_size=0)
    with pytest.raises(ValueError):
        ResponseStreamer(chunk_size=-1)


@pytest.mark.asyncio
async def test_policy_evaluator_cleanup_expired_removes_stale_counters():
    now = [0.0]
    storage = InMemoryStorage(time_provider=lambda: now[0])
    evaluator = PolicyEvaluator(storage=storage)
    rule = Rule(count=1, per="second", action=Reject())

    await evaluator.evaluate("client-a", "download", [rule])
    assert len(evaluator.request_counters) == 1

    now[0] = 10.0
    storage.cleanup_orphaned_counters({"download": [rule]})
    assert len(evaluator.request_counters) == 0


@pytest.mark.asyncio
async def test_policy_evaluator_cleanup_expired_removes_orphaned_counters():
    storage = InMemoryStorage(time_provider=lambda: 1.0)
    evaluator = PolicyEvaluator(storage=storage)
    rule = Rule(count=1, per="second", action=Reject())

    await evaluator.evaluate("client-a", "download", [rule])
    assert len(evaluator.request_counters) == 1

    storage.cleanup_orphaned_counters({})
    assert len(evaluator.request_counters) == 0


@pytest.mark.asyncio
async def test_policy_evaluator_selects_highest_priority_action():
    now = [0.0]
    evaluator = PolicyEvaluator(time_provider=lambda: now[0])
    rules = [
        Rule(count=1, per="second", action=Throttle(bytes_per_sec=100)),
        Rule(count=1, per="second", action=Reject()),
        Rule(count=1, per="second", action=Delay(seconds=0.5)),
    ]

    await evaluator.evaluate("client", "endpoint", rules)
    result = await evaluator.evaluate("client", "endpoint", rules)

    assert result is not None
    assert isinstance(result.rule.action, Reject)


@pytest.mark.asyncio
async def test_policy_evaluator_uses_custom_storage():
    class StubStorage(Storage):
        def __init__(self):
            self.calls = []

        async def get(self, key: str):
            return None

        async def set(self, key: str, value, expire=None):
            return None

        async def incr(self, key: str, expire=None):
            return 0

        async def delete(self, key: str):
            return None

        async def record_hit(self, request_key, handler_name, rule_index, window_seconds):
            self.calls.append((request_key, handler_name, rule_index, window_seconds))
            return SlidingWindowResult(hit_count=2, oldest_timestamp=1.0, current_timestamp=1.1)

    storage = StubStorage()
    evaluator = PolicyEvaluator(storage=storage)
    rule = Rule(count=1, per="second", action=Reject())

    result = await evaluator.evaluate("client-a", "download", [rule])

    assert result is not None
    assert isinstance(result.rule.action, Reject)
    assert storage.calls == [("client-a", "download", 0, 1)]


@pytest.mark.asyncio
async def test_limiter_remove_policy_cleans_in_memory_request_counters():
    storage = InMemoryStorage(time_provider=lambda: 1.0)
    limiter = ResponseBandwidthLimiter(storage=storage)
    rule = Rule(count=1, per="second", action=Reject())
    limiter.update_policy("download", [rule])

    await limiter._policy_evaluator.evaluate("client-a", "download", [rule])
    assert len(limiter._policy_evaluator.request_counters) == 1

    limiter.remove_policy("download")

    assert len(limiter._policy_evaluator.request_counters) == 0
