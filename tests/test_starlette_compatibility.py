"""Runtime-only ASGI checks, also run against the installed wheel with python -I.

No FastAPI, HTTP client, or pytest dependency is needed for this suite.
"""

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from starlette.applications import Starlette
from starlette.responses import FileResponse, PlainTextResponse, StreamingResponse
from starlette.routing import Mount, Route

from response_bandwidth_limiter import Reject, ResponseBandwidthLimiter, Rule
from response_bandwidth_limiter.storage import InMemoryStorage


async def request(app, path, method="GET"):
    messages = []
    sent_request = False

    async def receive():
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Future()

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    await asyncio.wait_for(app(scope, receive, send), timeout=5)
    return messages


class StarletteCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    def assert_response(self, messages, status, body):
        starts = [m for m in messages if m["type"] == "http.response.start"]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]["status"], status)
        parts = [m for m in messages if m["type"] == "http.response.body"]
        self.assertTrue(parts)
        self.assertEqual(b"".join(m.get("body", b"") for m in parts), body)
        self.assertTrue(all(m.get("more_body", False) for m in parts[:-1]))
        self.assertFalse(parts[-1].get("more_body", False))

    async def test_bandwidth_and_dynamic_mounted_route(self):
        limiter = ResponseBandwidthLimiter()

        @limiter.limit(10)
        async def download(request):
            return PlainTextResponse("x" * 25, headers={"X-Example": "preserved"})

        app = Starlette(routes=[Mount("/api", routes=[Route("/items/{item_id}", download)])])
        sleeps = []

        async def sleep(seconds):
            sleeps.append(seconds)

        with patch("response_bandwidth_limiter.middleware.asyncio.sleep", sleep):
            # Older Starlette versions build middleware eagerly in add_middleware().
            limiter.init_app(app, install_signal_handlers=False)
            messages = await request(app, "/api/items/42")
        self.assert_response(messages, 200, b"x" * 25)
        self.assertEqual(sleeps, [1.0, 1.0, 0.5])
        self.assertIn((b"x-example", b"preserved"), messages[0]["headers"])

    async def test_policy_rejects_and_runtime_update_removes_policy(self):
        limiter = ResponseBandwidthLimiter()

        @limiter.limit_rules([Rule(count=1, per="minute", action=Reject())])
        async def limited(request):
            return PlainTextResponse("ok")

        app = Starlette(routes=[Route("/limited", limited)])
        limiter.init_app(app, install_signal_handlers=False)
        self.assert_response(await request(app, "/limited"), 200, b"ok")
        rejected = await request(app, "/limited")
        self.assertEqual(rejected[0]["status"], 429)
        self.assertIn(b"retry-after", dict(rejected[0]["headers"]))
        limiter.remove_policy("limited")
        self.assert_response(await request(app, "/limited"), 200, b"ok")

    async def test_file_and_streaming_responses(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.txt"
            path.write_bytes(b"file payload")
            limiter = ResponseBandwidthLimiter()

            @limiter.limit(1000000)
            async def file_download(request):
                return FileResponse(path)

            @limiter.limit(1000000)
            async def stream_download(request):
                async def chunks():
                    yield b"first"
                    yield b"second"
                return StreamingResponse(chunks())

            app = Starlette(routes=[Route("/file", file_download), Route("/stream", stream_download)])
            limiter.init_app(app, install_signal_handlers=False)
            self.assert_response(await request(app, "/file"), 200, b"file payload")
            self.assert_response(await request(app, "/stream"), 200, b"firstsecond")

    async def test_lifespan_closes_storage(self):
        class ClosingStorage(InMemoryStorage):
            close_calls = 0

            async def close(self):
                self.close_calls += 1
                await super().close()

        storage = ClosingStorage()
        limiter = ResponseBandwidthLimiter(storage=storage)
        app = Starlette()
        limiter.init_app(app, install_signal_handlers=False)
        events = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
        messages = []

        async def receive():
            return next(events)

        async def send(message):
            messages.append(message)

        await app({"type": "lifespan", "asgi": {"version": "3.0"}}, receive, send)
        self.assertEqual([m["type"] for m in messages], ["lifespan.startup.complete", "lifespan.shutdown.complete"])
        self.assertEqual(storage.close_calls, 1)


if __name__ == "__main__":
    unittest.main()
