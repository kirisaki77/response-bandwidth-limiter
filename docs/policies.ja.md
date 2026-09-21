# リクエスト数の制限とスコープ

[READMEに戻る](../README.ja.md) | [English](policies.md) | [日本語](policies.ja.md)

## リクエスト数に応じた制限

`@limiter.limit_rules()` にルールを渡すと、一定期間のリクエスト数に応じて拒否・遅延・帯域制限を適用できます。各ルールには、リクエスト数の基準となる `count`、集計期間の `per`、適用する処理の `action` を指定します。既定ではIPアドレス単位で集計します。

```python
from datetime import timedelta

from fastapi import FastAPI, Request
from starlette.responses import PlainTextResponse

from response_bandwidth_limiter import Delay, Reject, ResponseBandwidthLimiter, Rule, Throttle

app = FastAPI()
limiter = ResponseBandwidthLimiter()

@app.get("/download")
@limiter.limit_rules([
    Rule(count=10, per="second", action=Throttle(bytes_per_sec=512)),
    Rule(count=30, per=timedelta(minutes=1), action=Delay(seconds=0.5)),
    Rule(count=200, per=timedelta(minutes=30), action=Reject(detail="同一IPからのリクエストが多すぎます")),
])
async def download_file(request: Request):
    return PlainTextResponse("payload" * 4096)

limiter.init_app(app)
```

### 複数のルールに一致した場合

ルールは独立して評価され、適用されるアクションは1つだけです。組み込みアクションの優先順位は次のとおりです。

| 優先順位 | アクション | 動作 |
| --- | --- | --- |
| 1 | `Reject(status_code=429, detail=...)` | エラーレスポンスを返す |
| 2 | `Delay(seconds=...)` | エンドポイントの実行前に待機する |
| 3 | `Throttle(bytes_per_sec=...)` | レスポンスの転送速度を制限する |

たとえば、`Throttle` と `Delay` の両方に一致すると、定義順にかかわらず `Delay` が選ばれます。同じ優先順位では `sort_key` を比較し、それも同じ場合に定義順を使います。詳しい選択条件は[APIリファレンス](api-reference.ja.md)を参照してください。

## 集計単位を変える

`Rule.scope` は、リクエストをどの単位で数えるかを指定します。

- `scope="ip"`: ミドルウェアが解決したクライアントIPアドレスを使います。これが既定値です。
- `scope="default"`: プロキシ設定を考慮する組み込みのクライアント識別処理を使います。識別できない場合は直接接続元のアドレス、それも取得できない場合は `"unknown"` を使います。
- 独自のスコープ名: `register_scope_resolver()` で登録した関数の戻り値を使います。

以下は、IPアドレス・APIキー・ユーザーごとに集計する例です。

```python
from fastapi import FastAPI, Request
from starlette.responses import PlainTextResponse

from response_bandwidth_limiter import Delay, Reject, ResponseBandwidthLimiter, Rule

app = FastAPI()
limiter = ResponseBandwidthLimiter(trusted_proxy_headers=True)
limiter.register_scope_resolver("api_key", lambda request: request.headers.get("X-Api-Key", "anonymous"))
limiter.register_scope_resolver("user", lambda request: request.headers.get("X-User-Id", "anonymous"))

@app.get("/download")
@limiter.limit_rules([
    Rule(count=5, per="second", action=Reject(detail="同一IPからのリクエストが多すぎます"), scope="ip"),
    Rule(count=20, per="minute", action=Reject(detail="同一APIキーからのリクエストが多すぎます"), scope="api_key"),
    Rule(count=3, per="second", action=Delay(seconds=0.25), scope="user"),
])
async def download(request: Request):
    return PlainTextResponse("ok")

limiter.init_app(app)
```

独自のスコープは、`limit_rules()` や `update_policy()` の前に登録してください。識別子を返す関数（リゾルバー）は同期関数に限られ、同じスコープ名を重複して登録することはできません。

リゾルバーで例外が発生した場合は警告を記録し、クライアントIPアドレスで集計します。IPアドレスの許可・拒否判定も、スコープの指定にかかわらずクライアントIPアドレスを使います。

`trusted_proxy_headers=True` を使う条件は、[プロキシ経由のIPアドレス](operations.ja.md#プロキシ経由のipアドレス)を参照してください。

---

[インストールと基本的な使い方](usage.ja.md) · [Redis・運用設定・移行](operations.ja.md) · [APIリファレンス](api-reference.ja.md) · [開発と配布物の検証](development.ja.md)
