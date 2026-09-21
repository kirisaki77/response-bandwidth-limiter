# Development and Distribution Checks

[Back to README](../README.md) | [English](development.md) | [日本語](development.ja.md)

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
