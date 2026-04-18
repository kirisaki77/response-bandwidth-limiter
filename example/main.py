import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI, Request
from response_bandwidth_limiter import Delay, Reject, ResponseBandwidthLimiter, Rule, Throttle

app = FastAPI()

limiter = ResponseBandwidthLimiter()
limiter.update_route("fast_response", 100)
limiter.update_route("slow_response", 10)
limiter.init_app(app)

@app.get("/fast")
async def fast_response():
    return {"message": "This is a fast response"}

@app.get("/slow")
async def slow_response():
    return {"message": "This response is slower"}


@app.get("/policy")
@limiter.limit_rules([
    Rule(count=2, per="second", action=Throttle(bytes_per_sec=32)),
    Rule(count=5, per="minute", action=Delay(seconds=0.25)),
    Rule(count=10, per="hour", action=Reject(detail="Too many requests from the same IP")),
])
async def policy_response(request: Request):
    return {
        "message": "This endpoint changes behavior based on request count per IP.",
        "rules": [
            "Over 2 requests/second -> throttle to 32 bytes/sec",
            "Over 5 requests/minute -> delay 0.25 seconds",
            "Over 10 requests/hour -> reject with 429",
        ],
    }

# DevTools Consoleでの確認例:
# const slowStart = performance.now();
# let res = await fetch("http://127.0.0.1:8000/slow", { cache: "no-store" });
# await res.text();
# const slowEnd = performance.now();
# console.log(`slow 通信時間: ${slowEnd - slowStart} ms`);
#
# const fastStart = performance.now();
# res = await fetch("http://127.0.0.1:8000/fast", { cache: "no-store" });
# await res.text();
# const fastEnd = performance.now();
# console.log(`fast 通信時間: ${fastEnd - fastStart} ms`);

# アプリケーション実行
if __name__ == "__main__":
    import uvicorn
    print("デモを開始します...")
    print("以下のURLで動作確認できます:")
    print("  - http://127.0.0.1:8000/ (使用方法)")
    print("  - http://127.0.0.1:8000/fast (制限を100 bytes/secに変更)")
    print("  - http://127.0.0.1:8000/slow (制限を10 bytes/secに変更)")
    print("  - http://127.0.0.1:8000/policy (IPごとの段階的policyのデモ)")
    print("ブラウザのDevTools Consoleで/slowと/fastの通信時間を比較できます")
    uvicorn.run(app, host="127.0.0.1", port=8000)
