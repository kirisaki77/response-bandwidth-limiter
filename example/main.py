from fastapi import FastAPI
from fastapi_bandwidth_limiter import EndpointResponseBandwidthLimiterMiddleware, set_response_bandwidth_limit

app = FastAPI()

# 帯域制限を適用するための辞書
endpoint_limits = {}

app.add_middleware(EndpointResponseBandwidthLimiterMiddleware, limits=endpoint_limits)

@app.get("/fast")
@set_response_bandwidth_limit(1024 * 100)  # 100KB/s
async def fast_response():
    return {"message": "This is a fast response"}

@app.get("/slow")
@set_response_bandwidth_limit(1024 * 10)  # 10KB/s
async def slow_response():
    return {"message": "This response is slower"}
