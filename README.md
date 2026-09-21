# Response Bandwidth Limiter

*Read this in other languages: [English](https://github.com/kirisaki77/response-bandwidth-limiter/blob/main/README.md), [日本語](https://github.com/kirisaki77/response-bandwidth-limiter/blob/main/README.ja.md)*

Response Bandwidth Limiter lets you configure a transfer speed limit for each endpoint in FastAPI and Starlette. The limit applies independently to each response.

It can also reject, delay, or throttle requests based on request counts. Group requests by IP address, API key, user, or another identifier, and use Redis to share counters across workers.

## Installation

Requires Python 3.10 or later. For the FastAPI example below, install the library, FastAPI, and the Uvicorn server:

```bash
python -m pip install response-bandwidth-limiter fastapi uvicorn
```

For Starlette applications, see [Installation and usage](https://github.com/kirisaki77/response-bandwidth-limiter/blob/main/docs/usage.md).

## Quick Start

Save the following as `main.py`:

```python
from fastapi import FastAPI, Request
from starlette.responses import PlainTextResponse

from response_bandwidth_limiter import ResponseBandwidthLimiter

app = FastAPI()
limiter = ResponseBandwidthLimiter()

@app.get("/download")
@limiter.limit(1024)  # 1024 bytes per second
async def download(request: Request):
    return PlainTextResponse("payload" * 4096)

limiter.init_app(app)
```

Register routes first, then call `limiter.init_app(app)` to install the middleware. Each response from `/download` is limited to 1,024 bytes per second; concurrent responses each have their own limit.

From the directory containing `main.py`, start the server:

```bash
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000/download](http://127.0.0.1:8000/download) in your browser. The response contains 28 KiB of repeated `payload` text and takes about 28 seconds to finish transferring at the configured rate. This delay is intentional.

## Key Considerations

- Request counters and IP block / allow state are process-local by default. Use Redis to share them across workers; runtime configuration updates still remain process-local.
- Enable `trusted_proxy_headers` only behind a trusted reverse proxy that rewrites or sanitizes client IP headers.
- `init_app()` handles shutdown signals by default. Set `install_signal_handlers=False` to manage shutdown in your application. See [Shutdown behavior](https://github.com/kirisaki77/response-bandwidth-limiter/blob/main/docs/usage.md#shutdown-behavior) for details.
- Actual transfer speed also depends on network conditions.

## Documentation

| Topic | Contents |
| --- | --- |
| [Installation and usage](https://github.com/kirisaki77/response-bandwidth-limiter/blob/main/docs/usage.md) | FastAPI, Starlette, optional dependencies, shutdown behavior |
| [Request-count policies and scopes](https://github.com/kirisaki77/response-bandwidth-limiter/blob/main/docs/policies.md) | `limit_rules`, action priority, IP / API-key / user grouping |
| [Redis, runtime updates, and migration](https://github.com/kirisaki77/response-bandwidth-limiter/blob/main/docs/operations.md) | Shared counters, endpoint identifiers, limitations, migration from `key_func` |
| [API reference](https://github.com/kirisaki77/response-bandwidth-limiter/blob/main/docs/api-reference.md) | Public methods, storage, rules, custom actions |
| [Development and distribution checks](https://github.com/kirisaki77/response-bandwidth-limiter/blob/main/docs/development.md) | Compatibility tests, wheel verification, release process |

Runnable examples are in [example/](https://github.com/kirisaki77/response-bandwidth-limiter/blob/main/example).

## Project

- [Source code](https://github.com/kirisaki77/response-bandwidth-limiter)
- [PyPI](https://pypi.org/project/response-bandwidth-limiter/)

## Acknowledgements

This library was inspired by [slowapi](https://github.com/laurentS/slowapi) (MIT Licensed).

## License

[MPL-2.0](https://github.com/kirisaki77/response-bandwidth-limiter/blob/main/LICENSE)
