# API Reference

[Back to README](../README.md) | [English](api-reference.md) | [日本語](api-reference.ja.md)

## `ResponseBandwidthLimiter`

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

`trusted_proxy_headers` defaults to `False`; see [Client IPs behind a proxy](operations.md#client-ips-behind-a-proxy) before enabling it. `storage` holds request counters and IP allow/block data and defaults to `InMemoryStorage`.

Decorators register configuration and preserve the endpoint’s original signature. See [Installation and usage](usage.md) for registration and shutdown behavior, and [Runtime updates](operations.md) for configuration changes and endpoint identifier resolution.

### Scope Registration

`register_scope_resolver(scope_name, resolver)` registers a synchronous function that returns a request’s grouping identifier.

- Register the scope before configuring its rules with `limit_rules()` or `update_policy()`.
- Leading and trailing whitespace is stripped from scope names during validation.
- Each name can be registered only once.
- The built-in names `ip` and `default` are reserved and cannot be overridden.

### Configuration and State

| Property | Value |
| --- | --- |
| `scope_resolvers` | Read-only mapping of registered scope names to resolvers |
| `routes` | Configured bandwidth limits |
| `policies` | Configured request-count rules |
| `configured_names` | Union of names with bandwidth limits or request-count policies |
| `storage` | The active `Storage` instance |
| `ip_manager` | The active `IPManager` instance |
| `shutdown_coordinator` | The `ShutdownCoordinator` instance managing shutdown |

## Storage

Each implementation inherits from `Storage`.

`InMemoryStorage(max_keys=10000)` evicts ordinary values when full, but preserves unexpired `ip:` control entries. If all slots hold IP control entries, adding a new key raises `StorageUnavailableError`; existing entries can still be updated or deleted. Increase capacity or use Redis when maintaining larger allow/block lists.

`ManagerStorage` reclaims expired values on writes, including old request-counter buckets, at most once per second per storage instance. Async reads and writes run blocking Manager IPC in worker threads. Cancellation of an awaiting task does not stop an already running write. This implementation remains experimental and is not intended for high load.

Custom `record_hit()` implementations return `SlidingWindowResult`. Its optional `retry_after_timestamp` is the timestamp of the hit whose expiry frees space for the next request; the evaluator adds the rule's window to calculate `Retry-After`. Omitting it preserves the `oldest_timestamp` fallback for existing storage implementations.

| Class | Purpose and constraints |
| --- | --- |
| `InMemoryStorage` | Default process-local storage with exact sliding-window counting |
| `ManagerStorage` | Experimental shared storage using `multiprocessing.Manager`; exact sliding-window behavior is not guaranteed |
| `RedisStorage` | Shared counters across workers and servers; requires Redis server 5.0 or later |

Create Redis-backed storage with `RedisStorage.from_url("redis://...")`. Supported options include:

- `counter_failure_mode`: `"open"`, `"closed"`, or `"local-memory-fallback"`.
- `control_failure_mode`: `"closed"` or `"local-memory-fallback"`.
- `key_hash=True`: hash only the request-identifier portion of the Redis key.

See [Redis, runtime updates, and migration](operations.md) for connection examples and operational limitations.

## `Rule` and Built-in Actions

```python
Rule(count: int, per: str | timedelta, action, scope: str = "ip")
Reject(status_code: int = 429, detail: str = "Rate limit exceeded")
Delay(seconds: float)
Throttle(bytes_per_sec: int)
```

### Windows and Scopes

- `per` accepts `second`, `minute`, `hour`, or a positive `datetime.timedelta` in whole seconds.
- `scope` defaults to `ip`. It accepts `ip`, `default`, or a registered custom name. Leading and trailing whitespace is stripped during validation.
- `Delay.seconds` must be positive and finite; NaN and infinity are rejected. During a delay, shutdown ABORT is checked every 0.1 seconds. Pending sleep is cancelled when the request is aborted or cancelled.

See [Request-count policies and scopes](policies.md) for grouping behavior and examples.

### Action Selection

When multiple rules match, select one action in this order:

1. Lowest `priority`. Built-in values are `Reject` (0), `Delay` (1), and `Throttle` (2).
2. Lowest `sort_key` when priorities match. For built-in actions, longer delays and lower throttle rates win.
3. Earlier position in `limit_rules([...])` when both values match.

## Custom Actions

Implement `ActionProtocol` and return a `PolicyDecision` from `decide()`. Required members are:

- `priority: int`
- `sort_key: int | float`
- `to_dict() -> dict[str, Any]`
- `decide(retry_after: int) -> PolicyDecision`

Built-in actions also expose `priority`, `sort_key`, and `to_dict()`. Custom actions participate in the same selection rules described above. `Action` is an alias of `ActionProtocol`.

`PolicyDecision` describes what to do when a rule matches:

| Field | Meaning |
| --- | --- |
| `reject` | Whether to return an error response immediately |
| `reject_status` | HTTP status code for rejection |
| `reject_detail` | Error detail in the JSON body |
| `retry_after` | Value for the `Retry-After` header |
| `pre_delay` | Delay before the endpoint runs |
| `throttle_rate` | Temporary response rate limit in bytes per second |

## Middleware and Utilities

`ResponseBandwidthLimiterMiddleware` applies bandwidth limits and request-count policies. In normal usage, register it with `limiter.init_app(app)`.

| Function | Returns |
| --- | --- |
| `get_endpoint_name(request)` | The request’s endpoint name |
| `get_route_path(request)` | The request’s route path |

Use the limiter’s `resolve_handler_identifier(request)` to find the identifier used for configuration lookups.

---

[Installation and Usage](usage.md) · [Request-Count Policies and Scopes](policies.md) · [Redis, Runtime Updates, and Migration](operations.md) · [Development and Distribution Checks](development.md)
