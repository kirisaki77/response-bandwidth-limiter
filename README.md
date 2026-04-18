# Response Bandwidth Limiter

*Read this in other languages: [English](README.md), [日本語](README.ja.md)*

A response bandwidth limiting middleware for FastAPI and Starlette. It allows you to limit response sending speed for specific endpoints and apply request-count based policies per IP.

## Installation

You can install using pip:

```bash
pip install response-bandwidth-limiter
```

### Dependencies

This library works with minimal dependencies, but requires FastAPI or Starlette for actual use.
Install as needed:

```bash
# When using with FastAPI
pip install fastapi

# When using with Starlette
pip install starlette

# Include dependencies needed for development and testing
pip install response-bandwidth-limiter[dev]
```

## Basic Usage

### Using Decorators (Recommended)

```python
from fastapi import FastAPI, Request
from starlette.responses import FileResponse
from response_bandwidth_limiter import ResponseBandwidthLimiter, ResponseBandwidthLimitExceeded, _response_bandwidth_limit_exceeded_handler

# Initialize the limiter
limiter = ResponseBandwidthLimiter()
app = FastAPI()

# Register with the application
app.state.response_bandwidth_limiter = limiter
app.add_exception_handler(ResponseBandwidthLimitExceeded, _response_bandwidth_limit_exceeded_handler)

# Response bandwidth limit for an endpoint (1024 bytes/sec)
@app.get("/download")
@limiter.limit(1024)  # 1024 bytes/sec
async def download_file(request: Request):
    return FileResponse("path/to/large_file.txt")

# Different limit for another endpoint (2048 bytes/sec)
@app.get("/video")
@limiter.limit(2048)  # 2048 bytes/sec
async def stream_video(request: Request):
    return FileResponse("path/to/video.mp4")
```

### Request-Count Policies with `limit_rules`

You can also apply step-based policies by IP. Each rule watches the request count within a time window and triggers one action.

```python
from fastapi import FastAPI, Request
from starlette.responses import PlainTextResponse
from response_bandwidth_limiter import (
    Delay,
    Reject,
    ResponseBandwidthLimiter,
    ResponseBandwidthLimiterMiddleware,
    Rule,
    Throttle,
)

app = FastAPI()
limiter = ResponseBandwidthLimiter()
app.state.response_bandwidth_limiter = limiter
app.add_middleware(ResponseBandwidthLimiterMiddleware)

@app.get("/download")
@limiter.limit_rules([
    Rule(count=10, per="second", action=Throttle(bytes_per_sec=512)),
    Rule(count=30, per="minute", action=Delay(seconds=0.5)),
    Rule(count=200, per="hour", action=Reject(detail="Too many downloads from the same IP")),
])
async def download_file(request: Request):
    return PlainTextResponse("payload" * 4096)
```

Available actions:

1. `Throttle(bytes_per_sec=...)`: slows the response stream.
2. `Delay(seconds=...)`: waits before the endpoint handler runs.
3. `Reject(status_code=429, detail=...)`: returns an error response.

### Usage with Starlette

```python
from starlette.applications import Starlette
from starlette.responses import FileResponse
from starlette.routing import Route
from response_bandwidth_limiter import ResponseBandwidthLimiter

# Decorator approach
limiter = ResponseBandwidthLimiter()

async def download_file(request):
    return FileResponse("path/to/large_file.txt")

# Apply decorator
download_with_limit = limiter.limit(1024)(download_file)

# Define routes
routes = [
    Route("/download", endpoint=download_with_limit)
]

app = Starlette(routes=routes)

# Register limiter with the app
limiter.init_app(app)
```

## Advanced Usage

### Setting Bandwidth Limit with Decorator (Simple Case)

For simply setting bandwidth limits, you can use the `set_response_bandwidth_limit` decorator:

```python
from fastapi import FastAPI
from starlette.responses import FileResponse
from response_bandwidth_limiter import set_response_bandwidth_limit

app = FastAPI()

@app.get("/download")
@set_response_bandwidth_limit(1024)  # 1024 bytes/sec
async def download_file():
    return FileResponse("path/to/large_file.txt")
```

This method allows you to set bandwidth limits directly on endpoints without initializing the `ResponseBandwidthLimiter` class.
Additionally, when using this decorator, you need to explicitly add the middleware:

```python
from response_bandwidth_limiter import ResponseBandwidthLimiterMiddleware

app = FastAPI()
app.add_middleware(ResponseBandwidthLimiterMiddleware)

@app.get("/download")
@set_response_bandwidth_limit(1024)
async def download_file():
    return FileResponse("path/to/large_file.txt")
```

This simple decorator uses global settings, so be careful when using the same function name in multiple applications. For more complex scenarios, the `ResponseBandwidthLimiter` class approach is recommended.

### Differences Between Simple and Standard Decorators

Key differences between the simple decorator (`set_response_bandwidth_limit`) and standard decorator (`ResponseBandwidthLimiter.limit`):

1. Simple decorator:
   - Uses global settings
   - Not dependent on app instance
   - May conflict when using same-named functions in multiple apps
   - Easy to configure

2. Standard decorator:
   - Isolated settings per app instance
   - Can be safely used across multiple apps
   - Requires more explicit initialization
   - Suitable for large applications

### Using Both Decorators Together

You can use both decorators in the same app:

```python
from fastapi import FastAPI, Request
from response_bandwidth_limiter import (
    ResponseBandwidthLimiter,
    set_response_bandwidth_limit,
    ResponseBandwidthLimiterMiddleware
)

app = FastAPI()
limiter = ResponseBandwidthLimiter()
app.state.response_bandwidth_limiter = limiter

# Add middleware only once
app.add_middleware(ResponseBandwidthLimiterMiddleware)

# Using standard decorator
@app.get("/video")
@limiter.limit(2048)
async def stream_video(request: Request):
    # ...

# Using simple decorator
@app.get("/download")
@set_response_bandwidth_limit(1024)
async def download_file(request: Request):
    # ...
```

### Dynamic Bandwidth Limiting

If you want to change bandwidth limits at runtime:

```python
limiter = ResponseBandwidthLimiter()
app = FastAPI()
app.state.response_bandwidth_limiter = limiter

@app.get("/admin/set-limit")
async def set_limit(endpoint: str, limit: int):
    limiter.routes[endpoint] = limit
    return {"status": "success", "endpoint": endpoint, "limit": limit}
```

The `/admin` endpoint above is intentionally a minimal example and does not include authentication or authorization. Do not expose this pattern as-is in production. Protect runtime configuration endpoints with your application's normal access controls.

**Important Note**: Bandwidth limit changes are persistent. Once you change an endpoint's bandwidth limit, that change will be maintained until the server restarts and applies to all subsequent requests. It's not a temporary change but a configuration update.

For example, if you change a limit from 1000 bytes/sec to 2000 bytes/sec, all subsequent requests will be processed with the 2000 bytes/sec limit. To revert to the original speed, you need to explicitly reset it.

### Dynamic Policy Updates

You can update request-count policies at runtime through `limiter.policies`.

```python
from response_bandwidth_limiter import Delay, Reject, Rule, Throttle

@app.get("/admin/set-policy")
async def set_policy(endpoint: str, mode: str):
    if mode == "throttle":
        limiter.policies[endpoint] = [
            Rule(count=5, per="second", action=Throttle(bytes_per_sec=256)),
            Rule(count=20, per="minute", action=Reject(detail="Too many requests")),
        ]
    elif mode == "delay":
        limiter.policies[endpoint] = [
            Rule(count=3, per="second", action=Delay(seconds=0.25)),
        ]
    else:
        limiter.policies.pop(endpoint, None)

    return {"status": "success", "endpoint": endpoint, "policies": endpoint in limiter.policies}
```

As with `limiter.routes`, policy changes remain active until you replace or remove them.

As with the dynamic limit example, `/admin/set-policy` is only a sample management endpoint. Add authentication and authorization before using a similar endpoint outside local development or internal tooling.

### Bandwidth Limits for Specific Users or IPs

```python
@app.get("/download/{user_id}")
@limiter.limit(1024)
async def download_for_user(request: Request, user_id: str):
    # If you want to apply different limits per user,
    # you can implement custom handling here
    user_limits = {
        "premium": 5120,
        "basic": 1024
    }
    user_type = get_user_type(user_id)
    actual_limit = user_limits.get(user_type, 512)
    # ...response processing
```

## Limitations and Considerations

- Bandwidth limits are applied server-side, so actual transfer speeds may vary depending on client-side bandwidth and network conditions.
- Be mindful of memory usage when transferring large files.
- In distributed systems, limits are applied per server.
- If request identity is determined from `X-Forwarded-For`, only trust that header when the application is behind a trusted reverse proxy that overwrites or sanitizes it. Otherwise, clients can spoof the header and bypass per-IP policies.

## API Reference

This section provides detailed reference for the main classes and methods provided by the library.

### ResponseBandwidthLimiter

The main class that provides response bandwidth limiting functionality.

```python
class ResponseBandwidthLimiter:
    def __init__(self, key_func=None):
        """
        Initialize response bandwidth limiting functionality
        
        Arguments:
            key_func: Key function for future extensions (not currently used)
        """
        
    def limit(self, rate: int):
        """
        Returns a decorator that applies bandwidth limits to endpoints
        
        Arguments:
            rate: Speed to limit (bytes/sec)
            
        Returns:
            Decorator function
            
        Exceptions:
            TypeError: If rate is not an integer
        """

    def limit_rules(self, rules):
        """
        Returns a decorator that applies request-count based rules to endpoints

        Arguments:
            rules: List of Rule objects evaluated per request

        Returns:
            Decorator function
        """
        
    def init_app(self, app):
        """
        Registers the limiter with a FastAPI or Starlette application
        
        Arguments:
            app: FastAPI or Starlette application instance
        """
```

### Rule, Throttle, Delay, Reject

```python
Rule(count: int, per: str, action, scope: str = "ip")
Throttle(bytes_per_sec: int)
Delay(seconds: float)
Reject(status_code: int = 429, detail: str = "Rate limit exceeded")
```

- `per` supports `second`, `minute`, and `hour`.
- `scope` currently supports `ip`.
- Rules are evaluated per endpoint and per client IP.

### ResponseBandwidthLimiterMiddleware

Middleware for FastAPI and Starlette that actually applies bandwidth limits.

```python
class ResponseBandwidthLimiterMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        """
        Initialize bandwidth limiting middleware
        
        Arguments:
            app: FastAPI or Starlette application
        """
        
    def get_handler_name(self, request, path):
        """
        Get handler name that matches the path
        
        Arguments:
            request: Request object
            path: Request path
            
        Returns:
            str or None: Endpoint name if it exists
        """
        
    async def dispatch(self, request, call_next):
        """
        Apply bandwidth limiting to the request
        
        Arguments:
            request: Request object
            call_next: Next middleware function
            
        Returns:
            Response object
        """
```

### set_response_bandwidth_limit

Simple bandwidth limiting decorator.

```python
def set_response_bandwidth_limit(limit: int):
    """
    Simple decorator for setting bandwidth limits per endpoint
    
    Arguments:
        limit: Speed to limit (bytes/sec)
        
    Returns:
        Decorator function
    """
```

### ResponseBandwidthLimitExceeded

Exception raised when bandwidth limit is exceeded.

```python
class ResponseBandwidthLimitExceeded(Exception):
    """
    Exception raised when bandwidth limit is exceeded
    
    Arguments:
        limit: Limit value (bytes/sec)
        endpoint: Endpoint name where the limit was applied
    """
```

### Error Handler

```python
async def _response_bandwidth_limit_exceeded_handler(request, exc):
    """
    Error handler for bandwidth limit exceeded
    
    Arguments:
        request: Request object
        exc: ResponseBandwidthLimitExceeded exception
        
    Returns:
        JSONResponse: With HTTP status code 429 and explanation
    """
```

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
