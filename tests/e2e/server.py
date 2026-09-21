"""Loopback-only application used by real HTTP tests against the installed package."""

import argparse
import asyncio
import os
from pathlib import Path
import socket

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from starlette.routing import Route

from response_bandwidth_limiter import (
    Delay, InMemoryStorage, Reject, ResponseBandwidthLimiter, Rule, ShutdownMode,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--redis-prefix")
    args = parser.parse_args()
    closed = args.directory / "closed"
    if args.redis_prefix:
        from response_bandwidth_limiter import RedisStorage

        storage = RedisStorage.from_url(
            os.environ["REDIS_URL"], prefix=args.redis_prefix, counter_failure_mode="closed",
        )
    else:
        storage = InMemoryStorage()
    close_storage = storage.close

    async def close():
        await close_storage()
        closed.write_text("closed", encoding="utf-8")

    storage.close = close
    limiter = ResponseBandwidthLimiter(storage=storage)
    payload = b"x" * 3072
    file_path = args.directory / "payload.bin"
    file_path.write_bytes(payload)

    async def health(request):
        return JSONResponse({"pid": os.getpid(), "in_flight": limiter.shutdown_coordinator.in_flight_count})

    @limiter.limit(1024)
    async def download(request):
        return PlainTextResponse(payload, headers={"X-E2E": "preserved"})

    @limiter.limit(3072)
    async def file_download(request):
        return FileResponse(file_path)

    @limiter.limit(1024)
    async def stream(request):
        async def chunks():
            for _ in range(3):
                yield b"x" * 1024
        return StreamingResponse(chunks())

    @limiter.limit(1024)
    async def endless(request):
        async def chunks():
            while True:
                yield b"x" * 1024
                await asyncio.sleep(0)
        return StreamingResponse(chunks())

    @limiter.limit_rules([Rule(1, "minute", Reject())])
    async def limited(request):
        return PlainTextResponse("accepted")

    @limiter.limit_rules([Rule(1, "minute", Delay(0.3))])
    async def delayed(request):
        return PlainTextResponse("delayed")

    async def shutdown(request: Request):
        limiter.begin_shutdown(ShutdownMode(request.path_params["mode"]))
        return PlainTextResponse("ok")

    async def block(request):
        await limiter.block_ip("127.0.0.1", duration=30)
        return PlainTextResponse("blocked")

    app = Starlette(routes=[
        Route("/health", health), Route("/download", download),
        Route("/file", file_download), Route("/stream", stream), Route("/endless", endless),
        Route("/limited", limited), Route("/delayed", delayed),
        Route("/shutdown/{mode}", shutdown, methods=["POST"]),
        Route("/block", block, methods=["POST"]),
    ])
    limiter.init_app(app)
    # Keep the bound socket open: there is no free-port discovery/rebind race.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        config = uvicorn.Config(app, host="127.0.0.1", port=port, loop="asyncio", http="h11",
                                lifespan="on", log_level="info", timeout_graceful_shutdown=10)
        (args.directory / "port").write_text(str(port), encoding="ascii")
        uvicorn.Server(config).run(sockets=[listener])


if __name__ == "__main__":
    main()
