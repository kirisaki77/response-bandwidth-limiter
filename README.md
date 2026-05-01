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

To share request-count policy counters across workers or processes with Redis:

```bash
pip install response-bandwidth-limiter[redis]
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

`init_app(app, install_signal_handlers=True)` also installs shutdown-aware `SIGINT` handling by default. The first `Ctrl+C` moves the limiter into drain mode, rejects new requests for routes configured with bandwidth limits or request-count policies with `503`, and lets existing throttled streaming responses continue. A second `Ctrl+C` promotes shutdown to abort mode and stops in-flight throttled streaming without waiting for the full response to finish. Set `install_signal_handlers=False` if you want to manage shutdown yourself.

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

### Shared Request-Count Counters with Redis

```python
import os

from fastapi import FastAPI, Request
from starlette.responses import PlainTextResponse

from response_bandwidth_limiter import RedisStorage, Reject, ResponseBandwidthLimiter, Rule

app = FastAPI()
limiter = ResponseBandwidthLimiter(
    storage=RedisStorage.from_url(os.environ["REDIS_URL"], counter_failure_mode="open", control_failure_mode="closed"),
    trusted_proxy_headers=True,
)

@app.get("/shared")
@limiter.limit_rules([Rule(count=5, per="second", action=Reject(detail="Too many requests from the same IP"))])
async def shared_policy(request: Request):
    return PlainTextResponse("shared counter")

limiter.init_app(app)
```

`RedisStorage` keeps request-count policy counters in Redis, so those counters can be shared across multiple workers, threads, or servers. The storage uses the same sliding-window semantics as the default in-memory evaluator. Redis server 5.0 or later is required. IP block / allow control data uses a separate failure policy and does not fail open by default.

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

### Choosing an endpoint identifier

The `endpoint` parameter in `update_route()` and `update_policy()` is an identifier, not only a function name.

- The limiter checks identifiers in this order: endpoint function name, `route.name`, route path template without the leading slash, then the base name of functions ending in `_response` or `_endpoint`.
- Decorators such as `@limiter.limit(...)` and `@limiter.limit_rules(...)` always register the endpoint function name first.
- For a dynamic route such as `/items/{item_id}`, the route-path identifier is `items/{item_id}`, not `/items/123`.
- `resolve_handler_identifier(request)` returns the identifier that the limiter would use for a specific request after `init_app()`.
- `get_endpoint_name(request)` and `get_route_path(request)` return raw request metadata and may differ from the resolved limiter identifier.

The admin endpoints above are intentionally minimal examples. Protect similar endpoints with your application's normal authentication and authorization.

For runnable examples, see [example/main.py](example/main.py), [example/dynamic_limit_example.py](example/dynamic_limit_example.py), [example/redis_shared_policy_example.py](example/redis_shared_policy_example.py), [example/ip_limiting_example.py](example/ip_limiting_example.py), and [example/custom_scope_example.py](example/custom_scope_example.py).

## Custom Request Scopes

`Rule.scope` can now choose how each request-count rule groups requests.

```python
from fastapi import FastAPI, Request
from starlette.responses import PlainTextResponse

from response_bandwidth_limiter import Delay, Reject, ResponseBandwidthLimiter, Rule

app = FastAPI()
limiter = ResponseBandwidthLimiter(trusted_proxy_headers=True)
limiter.register_scope_resolver("api_key", lambda request: request.headers.get("X-Api-Key", "anonymous"))
limiter.register_scope_resolver("user", lambda request: request.headers.get("X-User-Id", "anonymous"))

@app.get("/download")
@limiter.limit_rules([
    Rule(count=5, per="second", action=Reject(detail="Too many requests from the same IP"), scope="ip"),
    Rule(count=20, per="minute", action=Reject(detail="Too many requests for this API key"), scope="api_key"),
    Rule(count=3, per="second", action=Delay(seconds=0.25), scope="user"),
])
async def download(request: Request):
    return PlainTextResponse("ok")

limiter.init_app(app)
```

- `scope="ip"` always uses the real client IP resolved by the middleware.
- `scope="default"` uses the middleware's built-in proxy-aware client identifier, then falls back to the direct client address or `"unknown"`.
- Any other scope name must be registered through `register_scope_resolver()` before calling `limit_rules()` or `update_policy()`.
- Resolvers passed to `register_scope_resolver()` must be synchronous, and each custom scope name can be registered only once.
- If a registered custom resolver raises an exception, the middleware logs a warning and falls back to the real client IP.
- IP block / allow is unchanged and always uses the real client IP.
- API-key or user-specific grouping should use explicit custom scope names such as `api_key` or `user`.

## Migration Notes

- `key_func` has been removed.
- `scope="ip"` now means strict real-client-IP counting.
- Replace `key_func=...` with `register_scope_resolver("api_key", ...)` and point the rule at `scope="api_key"` or another explicit custom scope name.
- `scope="default"` now means the built-in proxy-aware client identifier.
- Custom scope names must be registered before `limit_rules()` or `update_policy()`.

## Limitations and Considerations

- Limits are applied server-side, so real transfer speed also depends on network conditions.
- Request-count policies and IP block / allow use `InMemoryStorage` by default, so state is not shared across processes or servers.
- `ManagerStorage` is experimental, slow, and not suitable for high-load environments. It does not guarantee consistency or exact sliding-window behavior.
- `RedisStorage` requires Redis server 5.0 or later.
- `update_policy()` and `update_route()` remain process-local runtime changes even when request counters are shared through Redis.
- `scope="default"` uses the built-in proxy-aware client identifier. `scope="ip"` and IP block / allow always use the real client IP.
- Custom scopes must be registered before `limit_rules()` or `update_policy()` runs, because unknown scopes fail fast during configuration.
- Custom scope resolvers must be synchronous, and duplicate scope names are rejected during registration.
- If request identity comes from `X-Forwarded-For`, only trust that header behind a trusted reverse proxy that rewrites or sanitizes it.
- Malformed proxy header values are ignored and the middleware falls back to the direct client address.
- If you migrate an existing Redis-backed rule from `key_func`-based grouping to an explicit custom scope such as `api_key`, the request-key portion of the Redis counter changes. Existing counter buckets expire naturally after their window passes.

## API Reference

### `ResponseBandwidthLimiter`

```python
class ResponseBandwidthLimiter:
    def __init__(self, trusted_proxy_headers: bool = False, storage: Storage | None = None): ...
    def register_scope_resolver(self, scope_name: str, resolver: ScopeResolver): ...
    def scope_resolvers(self) -> Mapping[str, ScopeResolver]: ...  # property
    def resolve_handler_identifier(self, request: Request) -> str | None: ...
    def limit(self, rate: int): ...
    def limit_rules(self, rules: list[Rule]): ...
    def init_app(self, app, install_signal_handlers: bool = True): ...
    def begin_shutdown(self, mode: ShutdownMode): ...
    async def shutdown(self, mode: ShutdownMode, timeout: float | None = None) -> bool: ...
    async def close(self) -> None: ...
    async def block_ip(self, ip: str, duration: int | None = None) -> None: ...
    async def unblock_ip(self, ip: str) -> None: ...
    async def is_blocked(self, ip: str) -> bool: ...
    async def allow_ip(self, ip: str) -> None: ...
    async def remove_allow(self, ip: str) -> None: ...
    async def is_allowed(self, ip: str) -> bool: ...
    def update_route(self, endpoint_name: str, rate: int): ...
    def remove_route(self, endpoint_name: str): ...
    def update_policy(self, endpoint_name: str, rules: list[Rule]): ...
    def remove_policy(self, endpoint_name: str): ...
    def get_limit(self, endpoint_name: str) -> int | None: ...
    def get_rules(self, endpoint_name: str) -> list[Rule]: ...
    @property
    def shutdown_coordinator(self) -> ShutdownCoordinator: ...
    @property
    def storage(self) -> Storage: ...
    @property
    def ip_manager(self) -> IPManager: ...
    @property
    def routes(self) -> Mapping[str, int]: ...
    @property
    def policies(self) -> Mapping[str, list[Rule]]: ...
    @property
    def configured_names(self) -> set[str]: ...
```

`trusted_proxy_headers` is `False` by default. Enable it only behind a trusted reverse proxy that rewrites `X-Forwarded-For` or `X-Real-IP`.
`storage` controls where request-count policy counters and IP control data are stored. If omitted, `InMemoryStorage` is used.
The decorators only register limiter configuration and preserve the endpoint's original signature.

- `register_scope_resolver(scope_name, resolver)` registers a custom request-count scope. Call it before `limit_rules()` or `update_policy()` if any rule uses that scope.
- Resolvers must be synchronous, and each custom scope name can be registered only once.
- Leading and trailing whitespace in scope names is stripped during validation.
- `scope_name="ip"` and `scope_name="default"` are reserved built-in scopes and cannot be overridden.
- `scope_resolvers` returns a read-only mapping of all registered custom scope names to their resolvers.
- `resolve_handler_identifier(request)` returns the identifier that the limiter would use for `update_route()` / `update_policy()` lookups. It requires `init_app()` or a request that already carries `scope["app"]`.
- Endpoint identifiers are resolved in this order: endpoint function name, `route.name`, route path template without the leading slash, then `_response` / `_endpoint` suffix stripping.

- `routes` exposes the currently configured bandwidth limits.
- `policies` exposes the currently configured request-count rules.
- `configured_names` returns the union of names configured by routes and policies.
- `storage` returns the `Storage` instance used by the limiter.
- `ip_manager` returns the `IPManager` instance used by the limiter.

### `Storage`, `InMemoryStorage`, `ManagerStorage`, `RedisStorage`

```python
class Storage: ...
class InMemoryStorage(Storage): ...
class ManagerStorage(Storage): ...
class RedisStorage(Storage): ...
```

- `InMemoryStorage` keeps exact sliding-window behavior but is process-local.
- `ManagerStorage` is an experimental `multiprocessing.Manager` based shared store. It does not guarantee exact sliding-window behavior.
- `RedisStorage.from_url("redis://...")` creates a Redis-backed storage that shares request counts across workers and servers.
- `RedisStorage` supports `counter_failure_mode="open" | "closed" | "local-memory-fallback"` and `control_failure_mode="closed" | "local-memory-fallback"`.
- `key_hash=True` hashes only the request-key tail of the Redis key when the raw request identifier would make keys too long.
- `RedisStorage` requires Redis server 5.0 or later.

### `Rule`, `Reject`, `Delay`, `Throttle`

```python
Rule(count: int, per: str | timedelta, action, scope: str = "ip")
Reject(status_code: int = 429, detail: str = "Rate limit exceeded")
Delay(seconds: float)
Throttle(bytes_per_sec: int)
```

- `per` supports `second`, `minute`, `hour`, and positive `datetime.timedelta` values.
- `timedelta` values must be whole-second durations.
- `scope` supports built-in `ip` and `default`, plus custom names registered with `register_scope_resolver()`.
- Leading and trailing whitespace in `scope` is stripped during validation.
- `scope="ip"` always counts by the real client IP.
- `scope="default"` uses the middleware's built-in proxy-aware client identifier, then falls back to the direct client address or `"unknown"`.
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
