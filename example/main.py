from fastapi import FastAPI, Request

import os
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from response_bandwidth_limiter import ResponseBandwidthLimiter

app = FastAPI()

# リミッターを初期化して登録
limiter = ResponseBandwidthLimiter()
app.state.response_bandwidth_limiter = limiter

# 各エンドポイントの制限を設定
limiter.routes["fast_response"] = 100  # 100 bytes/sec
limiter.routes["slow_response"] = 10   # 10 bytes/sec

# ミドルウェアを追加
from response_bandwidth_limiter import ResponseBandwidthLimiterMiddleware
app.add_middleware(ResponseBandwidthLimiterMiddleware)

@app.get("/fast")
async def fast_response():
    return {"message": "This is a fast response"}

@app.get("/slow")
async def slow_response():
    return {"message": "This response is slower"}
