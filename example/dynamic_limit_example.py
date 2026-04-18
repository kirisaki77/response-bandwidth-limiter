import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI, Request
from response_bandwidth_limiter import Delay, Reject, ResponseBandwidthLimiter, Rule, Throttle
from starlette.responses import PlainTextResponse

app = FastAPI()

limiter = ResponseBandwidthLimiter()
limiter.init_app(app)

# テスト用データのサイズ
data_size = 50000  # 50KB

@app.get("/data")
async def get_data():
    """大きめのデータを返すエンドポイント"""
    return PlainTextResponse("a" * data_size)

@app.get("/admin/set-limit")
async def set_limit(endpoint: str, limit: int):
    """帯域制限を動的に変更するエンドポイント"""
    limiter.update_route(endpoint, limit)
    return {
        "status": "success", 
        "message": f"{endpoint}の帯域制限を{limit} bytes/secに設定しました",
        "endpoint": endpoint, 
        "limit": limit
    }


def serialize_rule(rule: Rule) -> dict:
    return {
        "count": rule.count,
        "per": rule.per,
        "scope": rule.scope,
        "action": rule.action.to_dict(),
    }


@app.get("/admin/set-policy")
async def set_policy(endpoint: str, mode: str = "throttle"):
    """request count policy を動的に変更するエンドポイント"""
    if mode == "throttle":
        limiter.update_policy(endpoint, [
            Rule(count=2, per="second", action=Throttle(bytes_per_sec=256)),
            Rule(count=10, per="minute", action=Reject(detail="Too many requests from the same IP")),
        ])
    elif mode == "delay":
        limiter.update_policy(endpoint, [
            Rule(count=2, per="second", action=Delay(seconds=0.2)),
            Rule(count=10, per="minute", action=Reject(detail="Request burst detected")),
        ])
    elif mode == "clear":
        limiter.remove_policy(endpoint)
    else:
        return {
            "status": "error",
            "message": "mode は throttle, delay, clear のいずれかを指定してください",
        }

    return {
        "status": "success",
        "endpoint": endpoint,
        "mode": mode,
        "policies": [serialize_rule(rule) for rule in limiter.get_rules(endpoint)],
    }

@app.get("/")
async def info():
    """使用方法の説明"""
    return {
        "endpoints": {
            "/data": "大きめのデータを返します",
            "/admin/set-limit?endpoint=get_data&limit=1000": "get_dataエンドポイントの制限を1000 bytes/secに設定",
            "/admin/set-policy?endpoint=get_data&mode=throttle": "get_dataエンドポイントに段階的な throttle policy を設定",
            "/admin/set-policy?endpoint=get_data&mode=delay": "get_dataエンドポイントに delay policy を設定",
            "/admin/set-policy?endpoint=get_data&mode=clear": "get_dataエンドポイントの policy を削除",
        },
        "current_limits": limiter.routes,
        "current_policies": {
            endpoint: [serialize_rule(rule) for rule in rules]
            for endpoint, rules in limiter.policies.items()
        },
    }

limiter.update_route("get_data", 10000)

# アプリケーション実行
if __name__ == "__main__":
    import uvicorn
    print("動的帯域制限のデモを開始します...")
    print("以下のURLで動作確認できます:")
    print("  - http://127.0.0.1:8000/ (使用方法)")
    print("  - http://127.0.0.1:8000/data (データ取得)")
    print("  - http://127.0.0.1:8000/admin/set-limit?endpoint=get_data&limit=1000 (制限を1KB/secに変更)")
    print("  - http://127.0.0.1:8000/admin/set-policy?endpoint=get_data&mode=throttle (policyをthrottleに変更)")
    print("  - http://127.0.0.1:8000/admin/set-policy?endpoint=get_data&mode=delay (policyをdelayに変更)")
    print("  - http://127.0.0.1:8000/admin/set-policy?endpoint=get_data&mode=clear (policyを削除)")
    uvicorn.run(app, host="127.0.0.1", port=8000)
