"""Boundary cases found during the repository review."""

import asyncio
import multiprocessing
import threading
from unittest.mock import patch

import pytest
from starlette.applications import Starlette
from starlette.responses import FileResponse, PlainTextResponse
from starlette.routing import Mount, Route

from response_bandwidth_limiter import (
    InMemoryStorage, Reject, ResponseBandwidthLimiter, Rule, StorageUnavailableError,
)
from response_bandwidth_limiter.policy import PolicyEvaluator
from response_bandwidth_limiter.storage import ManagerStorage
from response_bandwidth_limiter.streaming import StreamingAbortedError


async def request(app, **overrides):
    messages = []
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": "/", "root_path": "",
        "query_string": b"", "headers": [], "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    scope.update(overrides)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    return messages


@pytest.mark.asyncio
async def test_file_extension_falls_back_to_throttled_body(tmp_path):
    path = tmp_path / "payload"
    path.write_bytes(b"x" * 25)
    limiter = ResponseBandwidthLimiter()
    seen_extensions = []

    @limiter.limit(10)
    async def download(request):
        seen_extensions.append(request.scope["extensions"])
        return FileResponse(path)

    app = Starlette(routes=[Route("/", download)])
    extensions = {"http.response.pathsend": {}, "http.response.zerocopysend": {}, "other": {}}
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    with patch("response_bandwidth_limiter.middleware.asyncio.sleep", sleep):
        limiter.init_app(app, install_signal_handlers=False)
        messages = await request(app, extensions=extensions)
    assert sleeps == [1.0, 1.0, 0.5]
    assert seen_extensions == [{"other": {}}]
    assert "http.response.pathsend" in extensions  # Do not mutate the server scope.
    assert {m["type"] for m in messages} == {"http.response.start", "http.response.body"}
    assert b"".join(m.get("body", b"") for m in messages) == b"x" * 25
    assert messages[-1]["more_body"] is False


@pytest.mark.asyncio
async def test_unlimited_route_preserves_file_extension(tmp_path):
    path = tmp_path / "payload"
    path.write_bytes(b"x")

    async def download(request):
        return FileResponse(path)

    app = Starlette(routes=[Route("/", download)])
    ResponseBandwidthLimiter().init_app(app, install_signal_handlers=False)
    messages = await request(app, extensions={"http.response.pathsend": {}})
    assert messages[-1]["type"] == "http.response.pathsend"


@pytest.mark.asyncio
@pytest.mark.parametrize("scope_name", ["ip", "default"])
@pytest.mark.parametrize("proxy", [False, True])
async def test_equivalent_ipv6_addresses_share_counter(scope_name, proxy):
    limiter = ResponseBandwidthLimiter(trusted_proxy_headers=proxy)

    @limiter.limit_rules([Rule(1, "minute", Reject(), scope=scope_name)])
    async def endpoint(request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/", endpoint)])
    limiter.init_app(app, install_signal_handlers=False)
    statuses = []
    for ip in ["2001:db8::1", "2001:0DB8:0:0:0:0:0:1"]:
        overrides = {"headers": [(b"x-forwarded-for", ip.encode())]} if proxy else {"client": (ip, 1234)}
        statuses.append((await request(app, **overrides))[0]["status"])
    assert statuses == [200, 429]


@pytest.mark.asyncio
@pytest.mark.parametrize("mounted", [False, True])
async def test_shadowed_route_policy_does_not_apply(mounted):
    limiter = ResponseBandwidthLimiter()

    async def actual(request):
        return PlainTextResponse("actual")

    @limiter.limit_rules([Rule(1, "minute", Reject())])
    async def shadowed(request):
        return PlainTextResponse("shadowed")

    first = Mount("/", routes=[Route("/", actual)]) if mounted else Route("/", actual)
    app = Starlette(routes=[first, Route("/", shadowed)])
    limiter.init_app(app, install_signal_handlers=False)
    for _ in range(2):
        messages = await request(app)
        assert messages[0]["status"] == 200
        assert messages[-1]["body"] == b"actual"


@pytest.mark.asyncio
async def test_ip_control_entries_survive_capacity_pressure():
    now = [0.0]
    storage = InMemoryStorage(max_keys=2, time_provider=lambda: now[0])
    limiter = ResponseBandwidthLimiter(storage=storage)
    await limiter.block_ip("192.0.2.1")
    await storage.set("disposable", 1)
    await limiter.block_ip("192.0.2.2", duration=10)
    assert await limiter.is_blocked("192.0.2.1")
    assert await storage.get("disposable") is None
    with pytest.raises(StorageUnavailableError):
        await limiter.allow_ip("192.0.2.3")
    assert await limiter.is_blocked("192.0.2.2")
    await limiter.block_ip("192.0.2.1")  # Existing entries can still be updated.
    now[0] = 11.0
    await limiter.allow_ip("192.0.2.3")
    assert await limiter.is_allowed("192.0.2.3")
    assert await limiter.is_blocked("192.0.2.1")


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 3])
async def test_retry_after_allows_next_request_without_intervening_traffic(count):
    now = [0.0]
    evaluator = PolicyEvaluator(InMemoryStorage(time_provider=lambda: now[0]))
    rules = [Rule(count, "minute", Reject())]
    for index in range(count):
        now[0] = index * 5.0
        assert await evaluator.evaluate({"ip": "a"}, "handler", rules) is None
    now[0] = 20.0
    rejected = await evaluator.evaluate({"ip": "a"}, "handler", rules)
    assert rejected is not None
    now[0] += rejected.retry_after
    assert await evaluator.evaluate({"ip": "a"}, "handler", rules) is None


@pytest.mark.asyncio
async def test_manager_storage_reclaims_old_time_buckets():
    now = [0.0]
    data = {}
    storage = ManagerStorage(data, threading.Lock(), time_provider=lambda: now[0])
    await storage.set("permanent", "keep")
    for index in range(100):
        now[0] = float(index)
        with patch("response_bandwidth_limiter.backends.base.time.time", return_value=now[0]):
            result = await storage.record_hit("a", "handler", 0, 1)
        assert result.hit_count == 1
    assert await storage.get("permanent") == "keep"
    assert len(data) <= 5  # Two live buckets, their expiry keys, and permanent data.


@pytest.mark.asyncio
@pytest.mark.parametrize("with_extensions", [False, True])
@pytest.mark.parametrize("failure", [None, RuntimeError, StreamingAbortedError, asyncio.CancelledError])
async def test_limited_route_preserves_outer_scope_and_restores_extensions(with_extensions, failure):
    from fastapi import FastAPI

    limiter = ResponseBandwidthLimiter()
    app = FastAPI()
    extensions = {"http.response.pathsend": {}, "http.response.zerocopysend": {}, "other": {}}
    observed = {}

    @app.get("/items/{item_id}")
    @limiter.limit(1000000)
    async def endpoint(item_id: str):
        if failure is not None:
            raise failure()
        return PlainTextResponse(item_id)

    limiter.init_app(app, install_signal_handlers=False)

    async def observer(scope, receive, send):
        try:
            await app(scope, receive, send)
        finally:
            observed.update(scope)

    overrides = {"extensions": extensions} if with_extensions else {}
    if failure in (RuntimeError, asyncio.CancelledError):
        with pytest.raises(failure):
            await request(observer, path="/items/42", **overrides)
    else:
        await request(observer, path="/items/42", **overrides)
    assert observed["route"].path == "/items/{item_id}"
    assert observed["endpoint"] is endpoint
    assert observed["path_params"] == {"item_id": "42"}
    if with_extensions:
        assert observed["extensions"] is extensions
    else:
        assert "extensions" not in observed
    assert limiter.shutdown_coordinator.in_flight_count == 0


@pytest.mark.asyncio
async def test_manager_sweep_uses_one_snapshot_and_skips_repeated_writes():
    class CountingDict(dict):
        snapshots = 0
        reads = 0

        def items(self):
            self.snapshots += 1
            return super().items()

        def get(self, *args):
            self.reads += 1
            return super().get(*args)

    now = [0.0]
    data = CountingDict()
    for index in range(3000):
        data[f"key-{index}"] = 1
        data[f"__rbl_exp__:key-{index}"] = 10.0
    storage = ManagerStorage(data, threading.Lock(), time_provider=lambda: now[0])
    for _ in range(10):
        await storage.incr("active", expire=60)
    assert data.snapshots == 1
    assert data.reads == 20  # Two reads per increment, independent of key count.
    now[0] = 11.0
    await storage.set("permanent", "keep")
    assert data.snapshots == 2
    assert set(data) == {"active", "__rbl_exp__:active", "permanent"}


@pytest.mark.asyncio
async def test_manager_proxy_io_runs_outside_event_loop_thread():
    loop_thread = threading.get_ident()

    class ThreadCheckedLock:
        def __init__(self, lock):
            self.lock = lock

        def __enter__(self):
            assert threading.get_ident() != loop_thread
            self.lock.acquire()

        def __exit__(self, *args):
            self.lock.release()

    with multiprocessing.Manager() as manager:
        data = manager.dict()
        storage = ManagerStorage(data, ThreadCheckedLock(manager.Lock()))
        await storage.set("value", 1, expire=60)
        assert await storage.get("value") == 1
        counts = await asyncio.gather(*(storage.incr("value") for _ in range(20)))
        assert sorted(counts) == list(range(2, 22))
        await storage.delete("value")
        assert await storage.get("value") is None
