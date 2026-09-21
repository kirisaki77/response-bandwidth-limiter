# Redis・運用設定・移行

[READMEに戻る](../README.ja.md) | [English](operations.md) | [日本語](operations.ja.md)

## Redisでカウンタを共有する

`RedisStorage` を使うと、複数のワーカー・スレッド・サーバーでリクエスト数のカウンタを共有できます。既定の `InMemoryStorage` と同じスライディングウィンドウ方式で集計します。Redisサーバー5.0以上と、[Redis用の追加依存関係](usage.ja.md)が必要です。

```python
import os

from fastapi import FastAPI, Request
from starlette.responses import PlainTextResponse

from response_bandwidth_limiter import RedisStorage, Reject, ResponseBandwidthLimiter, Rule

app = FastAPI()
limiter = ResponseBandwidthLimiter(
    storage=RedisStorage.from_url(os.environ["REDIS_URL"], counter_failure_mode="open", control_failure_mode="closed"),
    trusted_proxy_headers=True,
)

@app.get("/shared")
@limiter.limit_rules([Rule(count=5, per="second", action=Reject(detail="同一IPからのリクエストが多すぎます"))])
async def shared_policy(request: Request):
    return PlainTextResponse("shared counter")

limiter.init_app(app)
```

リクエスト数のカウンタと、IPアドレスの許可・拒否情報では、Redis接続障害時の動作を個別に設定できます。IPの許可・拒否情報は、既定では障害時にアクセスを拒否する設定（fail-closed）です。選択できる設定値は[APIリファレンス](api-reference.ja.md)を参照してください。

## 実行時の設定更新

設定を変更するときは、辞書を直接書き換えずにlimiterのメソッドを使います。以下は、登録済みの `download_file` に帯域制限とリクエスト数制限を設定し、その後それぞれ解除する例です。

```python
from response_bandwidth_limiter import Reject, Rule

limiter.update_route("download_file", 2048)
limiter.update_policy("download_file", [
    Rule(count=5, per="second", action=Reject()),
])

limiter.remove_route("download_file")
limiter.remove_policy("download_file")
```

これらの変更は、メソッドを呼び出したプロセスだけに反映されます。Redisでカウンタを共有していても、設定はプロセスごとに更新してください。管理用エンドポイントから更新する場合は、アプリケーションの認証・認可を適用してください。

### エンドポイントの識別子

`update_route()` と `update_policy()` の第1引数には、設定対象のエンドポイントを表す識別子を渡します。デコレータで登録した場合は、エンドポイントの関数名を指定してください。

リクエストを処理するときは、次の順で設定済みの識別子を探します。

1. エンドポイントの関数名
2. `route.name`
3. 先頭の `/` を除いたルートのパステンプレート
4. 関数名の末尾から `_response` または `_endpoint` を除いた名前

たとえば、`/items/{item_id}` のパステンプレートに対応する識別子は `items/{item_id}` です。実際のリクエスト先である `/items/123` は使いません。

`resolve_handler_identifier(request)` で、リクエストに対応する識別子を確認できます。`init_app()` 後、または `scope["app"]` が設定されたリクエストで使用してください。`get_endpoint_name()` と `get_route_path()` はリクエストの元の情報を返すため、最終的な識別子と異なる場合があります。

## プロキシ経由のIPアドレス

`trusted_proxy_headers` の既定値は `False` です。`X-Forwarded-For` や `X-Real-IP` を上書き・検証する、信頼できるリバースプロキシの背後でのみ `True` にしてください。不正なヘッダー値は無視され、直接接続元のアドレスが使われます。

## 制限事項

- 帯域制限はサーバー側で行うため、実際の転送速度はネットワーク状況にも依存します。
- 既定の `InMemoryStorage` は、リクエスト数のカウンタとIPの許可・拒否情報をプロセスごとに保持します。プロセス間・サーバー間では共有しません。
- `ManagerStorage` は実験的な実装です。低速で、一貫性や厳密なスライディングウィンドウの動作を保証しないため、高負荷の環境には適していません。

IP識別子は集計前に正規化されるため、同じIPv6アドレスの表記違いも同じカウンタになります。ルートは最初の完全一致に従い、後続の重複ルートの設定は適用しません。帯域制限対象のレスポンスではASGIのpath-send・zero-copy拡張を無効化し、ファイルのデータも制限処理を通します。制限対象外では拡張を維持します。

ポリシー更新時は、ローカルのRedisカウンタ名前空間と代替メモリのカウンタをリセットします。拒否されたリクエストも上限付きの履歴に残る場合があります。`Retry-After` は次のリクエスト分の空きを考慮します。同じルール・識別子への途中のアクセスがないことが前提で、別の一致ルールによってさらに待機が必要になる場合があります。

## `key_func` からの移行

`key_func` は削除されました。APIキーなどで集計していた場合は、次の手順で[独自のスコープ](policies.ja.md)に置き換えてください。

1. `register_scope_resolver("api_key", ...)` で識別子を返す関数を登録します。
2. ルールに `scope="api_key"` を指定します。
3. 登録後に `limit_rules()` または `update_policy()` を呼び出します。

`scope="ip"` は常にクライアントIPアドレスを使います。`scope="default"` はプロキシ設定を考慮する組み込みのクライアント識別処理を使います。

Redisを使っている場合は、移行によりカウンタのキーに含まれるリクエスト識別子が変わります。移行前のカウンタは、集計期間が過ぎると期限切れになります。

## 実行可能なサンプル

- [設定の動的な更新](../example/dynamic_limit_example.py)
- [Redisによる共有](../example/redis_shared_policy_example.py)
- [IPアドレスの許可・拒否](../example/ip_limiting_example.py)
- [独自のスコープ](../example/custom_scope_example.py)

---

[インストールと基本的な使い方](usage.ja.md) · [リクエスト数の制限とスコープ](policies.ja.md) · [APIリファレンス](api-reference.ja.md) · [開発と配布物の検証](development.ja.md)
