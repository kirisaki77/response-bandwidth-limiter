from fastapi import FastAPI, Request
import os
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from response_bandwidth_limiter import ResponseBandwidthLimiter, ResponseBandwidthLimiterMiddleware
from starlette.responses import PlainTextResponse

app = FastAPI()

# リミッターを初期化して登録
limiter = ResponseBandwidthLimiter()
app.state.response_bandwidth_limiter = limiter

# ミドルウェアを追加
app.add_middleware(ResponseBandwidthLimiterMiddleware)

# テスト用データのサイズ
data_size = 50000  # 50KB

@app.get("/data")
async def get_data():
    """大きめのデータを返すエンドポイント"""
    return PlainTextResponse("a" * data_size)

@app.get("/admin/set-limit")
async def set_limit(endpoint: str, limit: int):
    """帯域制限を動的に変更するエンドポイント"""
    limiter.routes[endpoint] = limit
    return {
        "status": "success", 
        "message": f"{endpoint}の帯域制限を{limit} bytes/secに設定しました",
        "endpoint": endpoint, 
        "limit": limit
    }

@app.get("/")
async def info():
    """使用方法の説明"""
    return {
        "endpoints": {
            "/data": "大きめのデータを返します",
            "/admin/set-limit?endpoint=get_data&limit=1000": "get_dataエンドポイントの制限を1000 bytes/secに設定",
        },
        "current_limits": limiter.routes
    }

# 初期設定 (10KB/sec)
limiter.routes["get_data"] = 10000

# アプリケーション実行
if __name__ == "__main__":
    import uvicorn
    print("動的帯域制限のデモを開始します...")
    print("以下のURLで動作確認できます:")
    print("  - http://127.0.0.1:8000/ (使用方法)")
    print("  - http://127.0.0.1:8000/data (データ取得)")
    print("  - http://127.0.0.1:8000/admin/set-limit?endpoint=get_data&limit=1000 (制限を1KB/secに変更)")
    uvicorn.run(app, host="127.0.0.1", port=8000)
