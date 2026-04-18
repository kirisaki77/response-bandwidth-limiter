# Response Bandwidth Limiter

*Read this in other languages: [English](README.md), [日本語](README.ja.md)*

Response Bandwidth Limiter is a FastAPI and Starlette middleware integration that throttles response transfer speed per endpoint and can apply request-count based policies per client.

## Installation

```bash
pip install response-bandwidth-limiter
```

Install a web framework alongside it:

```bash
pip install fastapi
# or
pip install starlette
```

For development and tests:

```bash
pip install response-bandwidth-limiter[dev]
```

## Basic Usage

### FastAPI

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

`init_app()` is the supported way to register the limiter. It attaches the middleware and stores the limiter on `app.state`.

### Request-Count Policies with `limit_rules`

```python
from fastapi import FastAPI, Request
from starlette.responses import PlainTextResponse

from response_bandwidth_limiter import Delay, Reject, ResponseBandwidthLimiter, Rule, Throttle

app = FastAPI()
limiter = ResponseBandwidthLimiter()

@app.get("/download")
@limiter.limit_rules([
    Rule(count=10, per="second", action=Throttle(bytes_per_sec=512)),
    Rule(count=30, per="minute", action=Delay(seconds=0.5)),
    Rule(count=200, per="hour", action=Reject(detail="Too many downloads from the same IP")),
])
async def download_file(request: Request):
    return PlainTextResponse("payload" * 4096)

limiter.init_app(app)
```

Available actions:

1. `Throttle(bytes_per_sec=...)`: slows the response stream.
2. `Delay(seconds=...)`: waits before the endpoint handler runs.
3. `Reject(status_code=429, detail=...)`: returns an error response.

### Starlette

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

## Runtime Updates

The limiter owns all configuration. Update it through methods instead of mutating dictionaries directly.

### Update a bandwidth limit

```python
@app.get("/admin/set-limit")
async def set_limit(endpoint: str, limit: int):
    limiter.update_route(endpoint, limit)
    return {"status": "success", "endpoint": endpoint, "limit": limit}
```

### Update request-count policies

```python
from response_bandwidth_limiter import Delay, Reject, Rule, Throttle

@app.get("/admin/set-policy")
async def set_policy(endpoint: str, mode: str):
    if mode == "throttle":
        limiter.update_policy(endpoint, [
            Rule(count=5, per="second", action=Throttle(bytes_per_sec=256)),
            Rule(count=20, per="minute", action=Reject(detail="Too many requests")),
        ])
    elif mode == "delay":
        limiter.update_policy(endpoint, [
            Rule(count=3, per="second", action=Delay(seconds=0.25)),
        ])
    else:
        limiter.remove_policy(endpoint)

    return {"status": "success", "endpoint": endpoint}
```

The admin endpoints above are intentionally minimal examples. Protect similar endpoints with your application's normal authentication and authorization.

For runnable examples, see [example/main.py](example/main.py) and [example/dynamic_limit_example.py](example/dynamic_limit_example.py).

## Limitations and Considerations

- Limits are applied server-side, so real transfer speed also depends on network conditions.
- Request-count policies are in-memory. In a distributed deployment, counters are not shared across processes or servers.
- If request identity comes from `X-Forwarded-For`, only trust that header behind a trusted reverse proxy that rewrites or sanitizes it.
- Malformed proxy header values are ignored and the middleware falls back to the direct client address.

## API Reference

### `ResponseBandwidthLimiter`

```python
class ResponseBandwidthLimiter:
    def __init__(self, key_func=None, trusted_proxy_headers: bool = False): ...
    def limit(self, rate: int): ...
    def limit_rules(self, rules: list[Rule]): ...
    def init_app(self, app): ...
    def update_route(self, endpoint_name: str, rate: int): ...
    def remove_route(self, endpoint_name: str): ...
    def update_policy(self, endpoint_name: str, rules: list[Rule]): ...
    def remove_policy(self, endpoint_name: str): ...
    def get_limit(self, endpoint_name: str) -> int | None: ...
    def get_rules(self, endpoint_name: str) -> list[Rule]: ...
    @property
    def routes(self) -> Mapping[str, int]: ...
    @property
    def policies(self) -> Mapping[str, list[Rule]]: ...
    @property
    def configured_names(self) -> set[str]: ...
```

`key_func` lets you override the client identifier used by request-count policies.
`trusted_proxy_headers` is `False` by default. Enable it only behind a trusted reverse proxy that rewrites `X-Forwarded-For` or `X-Real-IP`.
The decorators only register limiter configuration and preserve the endpoint's original signature.

- `routes` exposes the currently configured bandwidth limits.
- `policies` exposes the currently configured request-count rules.
- `configured_names` returns the union of names configured by routes and policies.

### `Rule`, `Throttle`, `Delay`, `Reject`

```python
Rule(count: int, per: str, action, scope: str = "ip")
Throttle(bytes_per_sec: int)
Delay(seconds: float)
Reject(status_code: int = 429, detail: str = "Rate limit exceeded")
```

- `per` supports `second`, `minute`, and `hour`.
- `scope` currently supports only `ip`.
- Action instances expose `priority` and `to_dict()`.

Custom policy actions can implement `ActionProtocol` and return a `PolicyDecision` from `decide()`.

`ActionProtocol` requires the following members:

- `priority: int`
- `sort_key: int | float`
- `to_dict() -> dict[str, Any]`
- `decide(retry_after: int) -> PolicyDecision`

`Action` is also exported as an alias of `ActionProtocol`.

`PolicyDecision` contains the fields used by the middleware when a rule matches:

- `reject`: whether to return an error response immediately.
- `reject_status`: the HTTP status code used when rejecting.
- `reject_detail`: the error detail returned in the JSON body.
- `retry_after`: the value written to the `Retry-After` header.
- `pre_delay`: a delay applied before the endpoint runs.
- `throttle_rate`: a temporary bytes-per-second rate applied to the response.

### `ResponseBandwidthLimiterMiddleware`

This is the middleware that applies throttling and request-count policies. In normal usage you should not add it manually; call `limiter.init_app(app)` instead.

### Utility Functions

```python
def get_endpoint_name(request):
    """
    Get endpoint name from request
    
    Arguments:
        request: Request object
    
    Returns:
        str: Endpoint name
    """
    
def get_route_path(request):
    """
    Get route path from request
    
    Arguments:
        request: Request object
        
    Returns:
        str: Route path
    """
```

## Source Code

The source code for this library is available at the following GitHub repository:
https://github.com/kirisaki77/response-bandwidth-limiter

## Acknowledgements

This library was inspired by [slowapi](https://github.com/laurentS/slowapi) (MIT Licensed).

## License

MPL-2.0

## PyPI

https://pypi.org/project/response-bandwidth-limiter/
