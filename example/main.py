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

# アプリケーション実行
if __name__ == "__main__":
    import uvicorn
    print("デモを開始します...")
    print("以下のURLで動作確認できます:")
    print("  - http://127.0.0.1:8000/ (使用方法)")
    print("  - http://127.0.0.1:8000/fast (制限を100 bytes/secに変更)")
    print("  - http://127.0.0.1:8000/slow (制限を10 bytes/secに変更)")
    uvicorn.run(app, host="127.0.0.1", port=8000)
