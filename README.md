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

`init_app(app, install_signal_handlers=True)` also installs shutdown-aware `SIGINT` handling by default. The first `Ctrl+C` moves the limiter into drain mode, rejects new throttled responses with `503`, and lets existing throttled responses continue. A second `Ctrl+C` promotes shutdown to abort mode and stops in-flight throttled streaming without waiting for the full response to finish. Set `install_signal_handlers=False` if you want to manage shutdown yourself.

### Request-Count Policies with `limit_rules`

```python
from datetime import timedelta

from fastapi import FastAPI, Request
from starlette.responses import PlainTextResponse

from response_bandwidth_limiter import Delay, Reject, ResponseBandwidthLimiter, Rule, Throttle

app = FastAPI()
limiter = ResponseBandwidthLimiter()

@app.get("/download")
@limiter.limit_rules([
    Rule(count=10, per="second", action=Throttle(bytes_per_sec=512)),
    Rule(count=30, per=timedelta(minutes=1), action=Delay(seconds=0.5)),
    Rule(count=200, per=timedelta(minutes=30), action=Reject(detail="Too many downloads from the same IP")),
])
async def download_file(request: Request):
    return PlainTextResponse("payload" * 4096)

limiter.init_app(app)
```

If multiple rules match the same request, the middleware evaluates those rules independently and applies only one action. The rules are not executed top-to-bottom. Selection uses action priority first, then `sort_key`, and finally the rule order in the `limit_rules([...])` list as a tiebreaker.

For example, if a request matches both a `Throttle` rule and a `Delay` rule, only `Delay` is applied even when the `Throttle` rule appears earlier in the list.

Available actions, ordered by selection priority when multiple rules match:

1. `Reject(status_code=429, detail=...)`: returns an error response.
2. `Delay(seconds=...)`: waits before the endpoint handler runs.
3. `Throttle(bytes_per_sec=...)`: slows the response stream.

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
from datetime import timedelta

from response_bandwidth_limiter import Delay, Reject, Rule, Throttle

@app.get("/admin/set-policy")
async def set_policy(endpoint: str, mode: str):
    if mode == "throttle":
        limiter.update_policy(endpoint, [
            Rule(count=5, per="second", action=Throttle(bytes_per_sec=256)),
            Rule(count=20, per=timedelta(minutes=30), action=Reject(detail="Too many requests")),
        ])
    elif mode == "delay":
        limiter.update_policy(endpoint, [
            Rule(count=3, per=timedelta(seconds=1), action=Delay(seconds=0.25)),
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
    def init_app(self, app, install_signal_handlers: bool = True): ...
    def begin_shutdown(self, mode: ShutdownMode): ...
    async def shutdown(self, mode: ShutdownMode, timeout: float | None = None) -> bool: ...
    def update_route(self, endpoint_name: str, rate: int): ...
    def remove_route(self, endpoint_name: str): ...
    def update_policy(self, endpoint_name: str, rules: list[Rule]): ...
    def remove_policy(self, endpoint_name: str): ...
    def get_limit(self, endpoint_name: str) -> int | None: ...
    def get_rules(self, endpoint_name: str) -> list[Rule]: ...
    @property
    def shutdown_coordinator(self) -> ShutdownCoordinator: ...
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

### `Rule`, `Reject`, `Delay`, `Throttle`

```python
Rule(count: int, per: str | timedelta, action, scope: str = "ip")
Reject(status_code: int = 429, detail: str = "Rate limit exceeded")
Delay(seconds: float)
Throttle(bytes_per_sec: int)
```

- `per` supports `second`, `minute`, `hour`, and positive `datetime.timedelta` values.
- `timedelta` values must be whole-second durations.
- `scope` currently supports only `ip`.
- Action instances expose `priority`, `sort_key`, and `to_dict()`.
- If multiple rules match the same request, the middleware evaluates those rules independently and selects a single action with the lowest `priority` value.
- The built-in priority order is `Reject` (0), `Delay` (1), then `Throttle` (2).
- If priorities are equal, the action with the lower `sort_key` wins. For the built-in actions, that means longer `Delay` values win over shorter ones, and lower `Throttle(bytes_per_sec=...)` values win over higher ones.
- The rule order in `limit_rules([...])` is only a tiebreaker. If both `priority` and `sort_key` are equal, the rule defined earlier in the list is selected.

Custom policy actions can implement `ActionProtocol` and return a `PolicyDecision` from `decide()`. Choose `priority` and `sort_key` values carefully, because the middleware uses them to resolve conflicts between multiple matched rules.

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
