import os
import sys
from datetime import timedelta

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI, Request
from starlette.responses import PlainTextResponse

from response_bandwidth_limiter import Delay, Reject, ResponseBandwidthLimiter, Rule

app = FastAPI()


def resolve_api_key(request: Request) -> str:
    return request.headers.get("X-Api-Key", "anonymous")


def resolve_user_id(request: Request) -> str:
    return request.headers.get("X-User-Id", "guest")


limiter = ResponseBandwidthLimiter(
    trusted_proxy_headers=True,
)
limiter.register_scope_resolver("api_key", resolve_api_key)
limiter.register_scope_resolver("user", resolve_user_id)


@app.get("/download")
@limiter.limit_rules([
    Rule(count=2, per="second", action=Reject(detail="Too many requests from the same IP"), scope="ip"),
    Rule(count=5, per=timedelta(minutes=1), action=Reject(detail="Too many requests for the same API key"), scope="api_key"),
    Rule(count=2, per="second", action=Delay(seconds=0.25), scope="user"),
])
async def download(request: Request):
    return PlainTextResponse(
        "custom scope demo\n"
        "- ip scope: strict per-IP counting\n"
        "- api_key scope: explicit API key grouping\n"
        "- user scope: custom registered resolver based counting\n"
        "- default scope remains available as the built-in proxy-aware client identifier\n"
    )


@app.get("/")
async def info():
    return {
        "endpoints": {
            "/download": "custom scope demo endpoint",
        },
        "headers": {
            "X-Forwarded-For": "real client IP when trusted_proxy_headers=True",
            "X-Api-Key": "used by scope=api_key via register_scope_resolver()",
            "X-User-Id": "used by scope=user via register_scope_resolver()",
        },
        "built_in_scopes": {
            "ip": "strict real-client-IP counting",
            "default": "built-in proxy-aware client identifier",
        },
        "rules": [
            {"scope": "ip", "count": 2, "per": "second", "action": "reject"},
            {"scope": "api_key", "count": 5, "per": "minute", "action": "reject"},
            {"scope": "user", "count": 2, "per": "second", "action": "delay"},
        ],
    }


limiter.init_app(app)


if __name__ == "__main__":
    import uvicorn

    print("Custom scope demo")
    print("  - http://127.0.0.1:8000/")
    print("  - http://127.0.0.1:8000/download")
    print("Try changing these headers while requesting /download:")
    print("  - X-Forwarded-For")
    print("  - X-Api-Key")
    print("  - X-User-Id")
    uvicorn.run(app, host="127.0.0.1", port=8000)
