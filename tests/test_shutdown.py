import asyncio
import signal

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse, StreamingResponse

from response_bandwidth_limiter import ResponseBandwidthLimiter, ShutdownMode
from response_bandwidth_limiter.middleware import ResponseBandwidthLimiterMiddleware
from response_bandwidth_limiter.shutdown import ShutdownCoordinator
from response_bandwidth_limiter.streaming import ResponseStreamer, StreamingAbortedError


def build_receive_once():
    state = {"sent": False}

    async def receive():
        if not state["sent"]:
            state["sent"] = True
            return {"type": "http.request", "body": b"", "more_body": False}

        await asyncio.Future()

    return receive


def test_shutdown_coordinator_tracks_state_and_in_flight():
    coordinator = ShutdownCoordinator()

    assert coordinator.is_shutting_down is False
    assert coordinator.mode is None
    assert coordinator.in_flight_count == 0

    coordinator.enter_response()
    coordinator.enter_response()
    coordinator.begin_shutdown(ShutdownMode.DRAIN)

    assert coordinator.is_shutting_down is True
    assert coordinator.mode is ShutdownMode.DRAIN
    assert coordinator.should_flush is True
    assert coordinator.should_abort is False
    assert coordinator.in_flight_count == 2

    coordinator.exit_response()
    coordinator.exit_response()
    coordinator.exit_response()

    assert coordinator.in_flight_count == 0


def test_shutdown_coordinator_wait_until_drained_and_reset():
    coordinator = ShutdownCoordinator()
    coordinator.enter_response()
    coordinator.begin_shutdown(ShutdownMode.ABORT)

    async def run_wait() -> bool:
        async def release() -> None:
            await asyncio.sleep(0.02)
            coordinator.exit_response()

        release_task = asyncio.create_task(release())
        try:
            return await coordinator.wait_until_drained(timeout=1.0)
        finally:
            await release_task

    assert asyncio.run(run_wait()) is True
    assert coordinator.should_abort is True

    coordinator.reset()

    assert coordinator.is_shutting_down is False
    assert coordinator.mode is None
    assert coordinator.in_flight_count == 0


def test_shutdown_coordinator_promotes_drain_to_abort():
    coordinator = ShutdownCoordinator()

    coordinator.begin_shutdown(ShutdownMode.DRAIN)
    coordinator.begin_shutdown(ShutdownMode.ABORT)
    coordinator.begin_shutdown(ShutdownMode.DRAIN)

    assert coordinator.mode is ShutdownMode.ABORT


def test_response_streamer_abort_check_raises_before_completion():
    state = {"calls": 0}
    streamer = ResponseStreamer(chunk_size=10, sleep_func=lambda _: asyncio.sleep(0))

    async def consume() -> None:
        async for _ in streamer.yield_limited_chunks(
            b"x" * 25,
            10,
            abort_check=lambda: state.__setitem__("calls", state["calls"] + 1) or state["calls"] > 1,
        ):
            pass

    try:
        asyncio.run(consume())
    except StreamingAbortedError:
        pass
    else:
        raise AssertionError("StreamingAbortedError was not raised")


def test_shutdown_rejects_new_requests_with_503():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    limiter.update_route("download", 128)
    limiter.init_app(app, install_signal_handlers=False)

    @app.get("/download")
    async def download():
        return PlainTextResponse("payload")

    limiter.begin_shutdown(ShutdownMode.DRAIN)

    response = TestClient(app).get("/download")

    assert response.status_code == 503
    assert response.json()["error"] == "Server shutting down"


def test_shutdown_drain_allows_existing_stream_to_finish():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    limiter.update_route("download", 10)
    limiter.init_app(app, install_signal_handlers=False)
    release_shutdown = asyncio.Event()

    async def stream_payload():
        yield b"a" * 10
        await release_shutdown.wait()
        yield b"b" * 10

    @app.get("/download")
    async def download():
        return StreamingResponse(stream_payload())

    async def run_request() -> list[dict]:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/download",
            "raw_path": b"/download",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "app": app,
        }
        messages = []
        receive = build_receive_once()

        async def send(message):
            messages.append(message)
            if message["type"] == "http.response.body" and message.get("body") == b"a" * 10:
                limiter.begin_shutdown(ShutdownMode.DRAIN)
                release_shutdown.set()

        await app(scope, receive, send)
        return messages

    messages = asyncio.run(run_request())
    body_messages = [message for message in messages if message["type"] == "http.response.body"]

    assert [message.get("body", b"") for message in body_messages] == [b"a" * 10, b"b" * 10, b""]


def test_shutdown_abort_stops_existing_stream_before_final_body_message():
    app = FastAPI()
    limiter = ResponseBandwidthLimiter()
    limiter.update_route("download", 10)
    limiter.init_app(app, install_signal_handlers=False)
    release_shutdown = asyncio.Event()

    async def stream_payload():
        yield b"a" * 10
        await release_shutdown.wait()
        yield b"b" * 10

    @app.get("/download")
    async def download():
        return StreamingResponse(stream_payload())

    async def run_request() -> list[dict]:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/download",
            "raw_path": b"/download",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "app": app,
        }
        messages = []
        receive = build_receive_once()

        async def send(message):
            messages.append(message)
            if message["type"] == "http.response.body" and message.get("body") == b"a" * 10:
                limiter.begin_shutdown(ShutdownMode.ABORT)
                release_shutdown.set()

        await app(scope, receive, send)
        return messages

    messages = asyncio.run(run_request())
    body_messages = [message for message in messages if message["type"] == "http.response.body"]

    assert [message.get("body", b"") for message in body_messages] == [b"a" * 10]


def test_limiter_shutdown_waits_for_drain_completion():
    limiter = ResponseBandwidthLimiter()
    limiter.shutdown_coordinator.enter_response()

    async def run_shutdown() -> bool:
        async def release() -> None:
            await asyncio.sleep(0.02)
            limiter.shutdown_coordinator.exit_response()

        release_task = asyncio.create_task(release())
        try:
            return await limiter.shutdown(ShutdownMode.DRAIN, timeout=1.0)
        finally:
            await release_task

    assert asyncio.run(run_shutdown()) is True


def test_middleware_signal_handler_upgrades_shutdown_mode(monkeypatch):
    app = FastAPI()
    middleware = ResponseBandwidthLimiterMiddleware(app)
    captured_handlers = {}
    original_calls = []

    def original_handler(signum, frame):
        original_calls.append((signum, frame))

    monkeypatch.setattr(signal, "getsignal", lambda sig: original_handler)
    monkeypatch.setattr(signal, "signal", lambda sig, handler: captured_handlers.__setitem__(sig, handler))

    middleware._install_signal_handler()
    middleware._handle_sigint(signal.SIGINT, None)
    middleware._handle_sigint(signal.SIGINT, None)
    middleware._restore_signal_handler()

    assert middleware.shutdown_coordinator.mode is ShutdownMode.ABORT
    assert len(original_calls) == 2
    assert signal.SIGINT in captured_handlers