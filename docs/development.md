# Development and Distribution Checks

[Back to README](../README.md) | [English](development.md) | [日本語](development.ja.md)

## Source layout

- `limiter.py`: public API, configuration, decorators, and application registration.
- `middleware.py`: ASGI request orchestration and response delivery.
- `routing.py`: route matching and endpoint identifiers.
- `identity.py`: client IP and policy scope resolution.
- `signals.py`: SIGINT registration and restoration; `shutdown.py` owns shutdown state.
- `models.py` / `policy.py`: rule and action types, and policy evaluation.
- `streaming.py`: bandwidth-limited chunk delivery.
- `backends/`: storage interfaces and memory, Manager, and Redis implementations.

`storage.py`, `redis_storage.py`, and `util.py` retain existing import paths. Operational storage warnings remain in `storage.py`. The Redis backend is loaded only when explicitly requested; importing the main package does not require Redis dependencies.

## Development Dependencies

Install the optional dependencies for development and testing:

```bash
pip install response-bandwidth-limiter[dev]
```

## Compatibility Tests

CI runs the full suite with pinned development dependencies on Windows and Linux using Python 3.10 and 3.14. A separate job checks the minimum supported Starlette version, 0.20.0, on both Python versions.

ASGI tests that do not depend on FastAPI or an HTTP test client cover:

- Bandwidth limits and request-count rejection
- Mounted dynamic routes and runtime policy removal
- File and streaming responses
- Resource cleanup when the application shuts down

Bandwidth regression tests verify that each chunk is sent immediately after its own pacing delay. They also check first-chunk timing, send intervals, and the final ASGI body flag.

## Real HTTP E2E tests

`tests/e2e/` starts Uvicorn in a separate process and exercises loopback HTTP bandwidth limiting, file and streaming responses, rejection and delay policies, disconnect cleanup, and drain/abort behavior. Linux also tests actual SIGINT delivery and storage closure. The Redis test shares counters and IP controls across two independent server processes.

The server runs with `python -I`, so install the current package before testing:

```console
python -m pip install -r requirements/dev.txt
python -m pip install .
python -m pytest tests/e2e -v -ra
```

Set `REDIS_URL` to a test Redis instance for the Redis case. Each test uses a unique key prefix and never flushes the database. SIGINT is skipped on Windows; Redis is skipped only when its URL is absent. Dedicated Linux CI jobs run on Python 3.10 and 3.14 with Redis 7 and fail if any E2E case is skipped. JUnit reports and server logs are saved as Actions artifacts.

Run only the regular suite with `python -m pytest -q --ignore=tests/e2e`.

## Distribution Checks

CI and the release workflow install the built wheel in a fresh virtual environment and run the same ASGI tests with isolated imports, without loading the source tree.

Run the check locally with the commands below. The `dist` directory must contain exactly one wheel:

```console
python -m build
python scripts/verify_wheel.py dist
```

The check needs access to the package index to install runtime dependencies.

## Releases

See the [release process (Japanese)](../RELEASING.md) for publishing instructions.

---

[Installation and Usage](usage.md) · [Request-Count Policies and Scopes](policies.md) · [Redis, Runtime Updates, and Migration](operations.md) · [API Reference](api-reference.md)
