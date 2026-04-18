from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse, StreamingResponse

from response_bandwidth_limiter import ResponseBandwidthLimiter


def test_fastapi_basic_integration(recorded_limit_calls):
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()

    @app.get("/slow")
    @limiter.limit(50)
    async def slow_endpoint(request: Request):
        return PlainTextResponse("a" * 150)

    @app.get("/fast")
    async def fast_endpoint():
        return PlainTextResponse("b" * 150)

    limiter.init_app(app)
    client = TestClient(app)

    assert "slow_endpoint" in limiter.routes
    assert limiter.get_limit("slow_endpoint") == 50

    slow_response = client.get("/slow")
    fast_response = client.get("/fast")

    assert slow_response.status_code == 200
    assert len(slow_response.content) == 150
    assert fast_response.status_code == 200
    assert len(fast_response.content) == 150
    assert [call["rate"] for call in recorded_limit_calls] == [50]


def test_fastapi_streaming_response(recorded_limit_calls):
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()

    async def number_generator():
        for index in range(5):
            yield f"data_packet{index}\n".encode("utf-8")

    @app.get("/stream")
    @limiter.limit(100)
    async def stream_endpoint(request: Request):
        return StreamingResponse(number_generator())

    limiter.init_app(app)
    response = TestClient(app).get("/stream")

    assert response.status_code == 200
    content = response.content
    assert b"data_packet0" in content
    assert b"data_packet4" in content
    assert [call["rate"] for call in recorded_limit_calls] == [100] * 5
