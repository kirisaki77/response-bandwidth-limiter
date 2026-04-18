from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse, StreamingResponse
from starlette.routing import Route
from starlette.testclient import TestClient as StarletteTestClient

from response_bandwidth_limiter import ResponseBandwidthLimiter


def test_fastapi_dynamic_bandwidth_limit(recorded_limit_calls):
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    limiter.init_app(app)

    data_size = 10000
    initial_limit = 5000
    new_limit = 500

    @app.get("/data")
    async def get_data():
        return PlainTextResponse("a" * data_size)

    @app.get("/admin/set-limit")
    async def set_limit(endpoint: str, limit: int):
        limiter.update_route(endpoint, limit)
        return {"status": "success", "endpoint": endpoint, "limit": limit}

    client = TestClient(app)
    limiter.update_route("get_data", initial_limit)

    response = client.get("/data")

    assert response.status_code == 200
    assert len(response.content) == data_size
    assert [call["rate"] for call in recorded_limit_calls] == [initial_limit]

    client.get(f"/admin/set-limit?endpoint=get_data&limit={new_limit}")

    assert limiter.get_limit("get_data") == new_limit

    recorded_limit_calls.clear()
    response = client.get("/data")

    assert response.status_code == 200
    assert len(response.content) == data_size
    assert [call["rate"] for call in recorded_limit_calls] == [new_limit]


def test_starlette_dynamic_bandwidth_limit(recorded_limit_calls):
    limiter = ResponseBandwidthLimiter()
    data_size = 10000

    async def get_data(request):
        return PlainTextResponse("a" * data_size)

    async def set_limit(request):
        endpoint = request.query_params.get("endpoint")
        limit = int(request.query_params.get("limit"))
        limiter.update_route(endpoint, limit)
        return JSONResponse({"success": True, "endpoint": endpoint, "limit": limit})

    app = Starlette(routes=[Route("/data", endpoint=get_data), Route("/admin/set-limit", endpoint=set_limit)])
    limiter.init_app(app)

    client = StarletteTestClient(app)
    initial_limit = 5000
    new_limit = 500

    limiter.update_route("get_data", initial_limit)

    response = client.get("/data")

    assert response.status_code == 200
    assert len(response.content) == data_size
    assert [call["rate"] for call in recorded_limit_calls] == [initial_limit]

    client.get(f"/admin/set-limit?endpoint=get_data&limit={new_limit}")

    assert limiter.get_limit("get_data") == new_limit

    recorded_limit_calls.clear()
    response = client.get("/data")

    assert response.status_code == 200
    assert len(response.content) == data_size
    assert [call["rate"] for call in recorded_limit_calls] == [new_limit]


def test_streaming_dynamic_bandwidth_limit(recorded_limit_calls):
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    limiter.init_app(app)

    chunk_size = 1000
    chunks = 5

    async def number_generator():
        for _ in range(chunks):
            yield ("x" * chunk_size).encode("utf-8")

    @app.get("/stream")
    async def stream_data():
        return StreamingResponse(number_generator())

    @app.get("/admin/set-stream-limit")
    async def set_stream_limit(limit: int):
        limiter.update_route("stream_data", limit)
        return {"status": "success", "endpoint": "stream_data", "limit": limit}

    client = TestClient(app)
    initial_limit = 2000
    new_limit = 500

    limiter.update_route("stream_data", initial_limit)

    response = client.get("/stream")

    assert response.status_code == 200
    assert len(response.content) == chunk_size * chunks
    assert [call["rate"] for call in recorded_limit_calls] == [initial_limit] * chunks

    client.get(f"/admin/set-stream-limit?limit={new_limit}")

    assert limiter.get_limit("stream_data") == new_limit

    recorded_limit_calls.clear()
    response = client.get("/stream")

    assert response.status_code == 200
    assert len(response.content) == chunk_size * chunks
    assert [call["rate"] for call in recorded_limit_calls] == [new_limit] * chunks
