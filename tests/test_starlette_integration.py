from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, StreamingResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from response_bandwidth_limiter import ResponseBandwidthLimiter


def test_starlette_basic_integration(recorded_limit_calls):
    limiter = ResponseBandwidthLimiter()

    async def slow_endpoint(request):
        return PlainTextResponse("a" * 200)

    async def fast_endpoint(request):
        return PlainTextResponse("b" * 200)

    slow_with_limit = limiter.limit(100)(slow_endpoint)
    app = Starlette(routes=[Route("/slow", endpoint=slow_with_limit), Route("/fast", endpoint=fast_endpoint)])
    limiter.init_app(app)

    client = TestClient(app)
    slow_response = client.get("/slow")
    fast_response = client.get("/fast")

    assert slow_response.status_code == 200
    assert len(slow_response.content) == 200
    assert fast_response.status_code == 200
    assert len(fast_response.content) == 200
    assert "slow_endpoint" in limiter.routes
    assert [call["rate"] for call in recorded_limit_calls] == [100]


def test_starlette_streaming_response(recorded_limit_calls):
    limiter = ResponseBandwidthLimiter()

    async def number_generator():
        for index in range(5):
            yield f"data_packet{index}\n".encode("utf-8")

    async def stream_endpoint(request):
        return StreamingResponse(number_generator())

    stream_with_limit = limiter.limit(100)(stream_endpoint)
    app = Starlette(routes=[Route("/stream", endpoint=stream_with_limit)])
    limiter.init_app(app)

    response = TestClient(app).get("/stream")

    assert response.status_code == 200
    content = response.content
    assert b"data_packet0" in content
    assert b"data_packet4" in content
    assert [call["rate"] for call in recorded_limit_calls] == [100] * 5
