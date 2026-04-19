import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI, Request
from starlette.responses import PlainTextResponse

from response_bandwidth_limiter import RedisStorage, Reject, ResponseBandwidthLimiter, Rule

app = FastAPI()

redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
limiter = ResponseBandwidthLimiter(
    storage=RedisStorage.from_url(redis_url, counter_failure_mode="open", control_failure_mode="closed"),
    trusted_proxy_headers=True,
)


@app.get("/shared")
@limiter.limit_rules([
    Rule(count=2, per="second", action=Reject(detail="Too many shared requests from the same IP")),
])
async def shared_policy(request: Request):
    return PlainTextResponse(
        "Redis-backed request counter demo\n"
        "The request-count policy is shared across workers and processes that point at the same Redis instance.\n"
        "Runtime update_policy()/update_route() calls are still process-local.\n"
    )


limiter.init_app(app)


if __name__ == "__main__":
    import uvicorn

    print("Redis-backed policy demo")
    print(f"REDIS_URL={redis_url}")
    print("  - http://127.0.0.1:8000/shared")
    uvicorn.run(app, host="127.0.0.1", port=8000)
