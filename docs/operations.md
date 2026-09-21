# Redis, Runtime Updates, and Migration

[Back to README](../README.md) | [English](operations.md) | [日本語](operations.ja.md)

## Share Counters with Redis

`RedisStorage` shares request counters across workers, threads, and servers, using the same sliding-window semantics as the default `InMemoryStorage`. It requires Redis server 5.0 or later and the [optional Redis dependency](usage.md).

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

Request counters and IP allow/block data have separate policies for Redis failures. IP allow/block data defaults to fail-closed behavior. See the [API reference](api-reference.md) for supported settings.

## Runtime Updates

Use the limiter’s methods to change configuration. This example sets a bandwidth limit and a request-count policy for the registered `download_file` endpoint, then removes each setting:

```python
from response_bandwidth_limiter import Reject, Rule

limiter.update_route("download_file", 2048)
limiter.update_policy("download_file", [
    Rule(count=5, per="second", action=Reject()),
])

limiter.remove_route("download_file")
limiter.remove_policy("download_file")
```

Updates apply only to the process that calls the method, even when Redis shares the counters. Update configuration in each process. If you expose these methods through admin endpoints, apply your application’s authentication and authorization.

### Endpoint Identifiers

The first argument to `update_route()` and `update_policy()` identifies the endpoint to configure. For endpoints registered with decorators, use the endpoint function name.

When handling a request, the limiter looks for a configured identifier in this order:

1. Endpoint function name
2. `route.name`
3. Route path template without the leading `/`
4. Function name with a trailing `_response` or `_endpoint` removed

For example, the path identifier for `/items/{item_id}` is `items/{item_id}`, not a concrete request path such as `/items/123`.

Use `resolve_handler_identifier(request)` to inspect the resolved identifier after `init_app()`, or with a request that has `scope["app"]`. The `get_endpoint_name()` and `get_route_path()` helpers return raw request metadata, which may differ from the resolved identifier.

## Client IPs behind a Proxy

`trusted_proxy_headers` defaults to `False`. Enable it only behind a trusted reverse proxy that rewrites or sanitizes `X-Forwarded-For` or `X-Real-IP`. Malformed header values are ignored, and the middleware falls back to the direct client address.

## Limitations

- Limits are applied server-side; actual transfer speed also depends on network conditions.
- The default `InMemoryStorage` keeps request counters and IP allow/block data within each process. It does not share state across processes or servers.
- `ManagerStorage` is experimental and slow. It does not guarantee consistency or exact sliding-window behavior and is unsuitable for high-load environments.

## Migrate from `key_func`

`key_func` has been removed. To keep grouping requests by API key or another identifier, replace it with a [custom scope](policies.md):

1. Register a resolver with `register_scope_resolver("api_key", ...)`.
2. Set `scope="api_key"` on the rule.
3. Call `limit_rules()` or `update_policy()` after registration.

`scope="ip"` always uses the client IP. `scope="default"` uses the built-in client identifier, which accounts for proxy settings.

For Redis-backed rules, this migration changes the request identifier in the counter key. Old counter buckets expire after their window passes.

## Runnable Examples

- [Runtime configuration updates](../example/dynamic_limit_example.py)
- [Shared Redis counters](../example/redis_shared_policy_example.py)
- [IP allow/block rules](../example/ip_limiting_example.py)
- [Custom scopes](../example/custom_scope_example.py)

---

[Installation and Usage](usage.md) · [Request-Count Policies and Scopes](policies.md) · [API Reference](api-reference.md) · [Development and Distribution Checks](development.md)
