import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI, Request
from starlette.responses import PlainTextResponse

from response_bandwidth_limiter import Reject, ResponseBandwidthLimiter, Rule

app = FastAPI()

limiter = ResponseBandwidthLimiter(trusted_proxy_headers=True)


@app.get("/limited")
@limiter.limit_rules([Rule(count=2, per="second", action=Reject(detail="Too many requests from the same IP"))])
async def limited(request: Request):
    return PlainTextResponse("ok")


@app.get("/admin/block")
async def block(ip: str):
    await limiter.block_ip(ip)
    return {"status": "blocked", "ip": ip}


@app.get("/admin/allow")
async def allow(ip: str):
    await limiter.allow_ip(ip)
    return {"status": "allowed", "ip": ip}


@app.get("/admin/redis-config")
async def redis_config(url: str = "redis://127.0.0.1:6379/0"):
    from response_bandwidth_limiter import RedisStorage

    _ = RedisStorage.from_url(url, counter_failure_mode="open", control_failure_mode="closed")
    return {
        "status": "example",
        "message": "Create a new ResponseBandwidthLimiter with RedisStorage during app startup.",
        "url": url,
    }


limiter.init_app(app)


if __name__ == "__main__":
    import uvicorn

    print("IP limiting demo")
    print("  - http://127.0.0.1:8000/limited")
    print("  - http://127.0.0.1:8000/admin/block?ip=203.0.113.10")
    print("  - http://127.0.0.1:8000/admin/allow?ip=203.0.113.10")
    print("  - http://127.0.0.1:8000/admin/redis-config")
    uvicorn.run(app, host="127.0.0.1", port=8000)