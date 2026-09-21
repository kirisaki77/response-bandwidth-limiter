# Installation and Usage

[Back to README](../README.md) | [English](usage.md) | [日本語](usage.ja.md)

## Installation

Requires Python 3.10 or later. Install the library and the framework you use:

```bash
pip install response-bandwidth-limiter
```

```bash
pip install fastapi
# or
pip install starlette
```

To share request counters through Redis, install the optional dependency below. See [Redis, runtime updates, and migration](operations.md) for connection settings.

```bash
pip install response-bandwidth-limiter[redis]
```

## FastAPI

Pass a rate in bytes per second to `@limiter.limit()`. This example limits downloads to 1,024 bytes per second and video to 2,048 bytes per second. Replace the paths with files on your server.

```python
from fastapi import FastAPI, Request
from starlette.responses import FileResponse

from response_bandwidth_limiter import ResponseBandwidthLimiter

app = FastAPI()
limiter = ResponseBandwidthLimiter()

@app.get("/download")
@limiter.limit(1024)
async def download_file(request: Request):
    return FileResponse("path/to/large_file.txt")

@app.get("/video")
@limiter.limit(2048)
async def stream_video(request: Request):
    return FileResponse("path/to/video.mp4")

limiter.init_app(app)
```

Call `limiter.init_app(app)` after defining the routes. It adds the middleware and stores the limiter on `app.state`.

## Starlette

Pass the function with its limit configured to `Route` as the endpoint:

```python
from starlette.applications import Starlette
from starlette.responses import FileResponse
from starlette.routing import Route

from response_bandwidth_limiter import ResponseBandwidthLimiter

limiter = ResponseBandwidthLimiter()

async def download_file(request):
    return FileResponse("path/to/large_file.txt")

routes = [
    Route("/download", endpoint=limiter.limit(1024)(download_file)),
]

app = Starlette(routes=routes)
limiter.init_app(app)
```

## Shutdown Behavior

By default, `init_app()` registers a `SIGINT` handler:

1. The first `Ctrl+C` enters drain mode. New requests to routes with bandwidth limits or request-count policies receive `503`; existing throttled streams continue.
2. The second `Ctrl+C` enters abort mode and stops in-flight throttled streams.

To manage shutdown in your application, use `init_app(app, install_signal_handlers=False)`.

---

[Request-Count Policies and Scopes](policies.md) · [Redis, Runtime Updates, and Migration](operations.md) · [API Reference](api-reference.md) · [Development and Distribution Checks](development.md)
