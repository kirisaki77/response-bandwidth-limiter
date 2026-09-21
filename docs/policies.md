# Request-Count Policies and Scopes

[Back to README](../README.md) | [English](policies.md) | [日本語](policies.ja.md)

## Limit Requests by Count

Pass rules to `@limiter.limit_rules()` to reject, delay, or throttle requests based on their count within a time window. Each rule specifies a request-count threshold (`count`), a window (`per`), and an `action`. Requests are counted by IP address by default.

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

### When Multiple Rules Match

Rules are evaluated independently, and only one action is applied. Built-in actions have the following order of precedence:

| Precedence | Action | Behavior |
| --- | --- | --- |
| 1 | `Reject(status_code=429, detail=...)` | Return an error response |
| 2 | `Delay(seconds=...)` | Wait before running the endpoint |
| 3 | `Throttle(bytes_per_sec=...)` | Limit response transfer speed |

For example, when both `Throttle` and `Delay` match, `Delay` wins regardless of rule order. Actions with equal priority are compared by `sort_key`; rule order breaks any remaining tie. See the [API reference](api-reference.md) for the full selection rules.

## Choose a Request Scope

`Rule.scope` determines how requests are grouped for counting:

- `scope="ip"`: use the client IP resolved by the middleware. This is the default.
- `scope="default"`: use the built-in client identifier, which accounts for proxy settings. Fall back to the direct client address, then to `"unknown"` if no address is available.
- A custom scope name: use the value returned by a function registered with `register_scope_resolver()`.

This example counts requests by IP address, API key, and user:

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

Register custom scopes before calling `limit_rules()` or `update_policy()`. Resolver functions must be synchronous, and each scope name can be registered only once.

If a resolver raises an exception, the middleware logs a warning and counts by client IP instead. IP allow/block checks always use the client IP, regardless of the rule’s scope.

See [Client IPs behind a proxy](operations.md#client-ips-behind-a-proxy) for when to enable `trusted_proxy_headers=True`.

---

[Installation and Usage](usage.md) · [Redis, Runtime Updates, and Migration](operations.md) · [API Reference](api-reference.md) · [Development and Distribution Checks](development.md)
