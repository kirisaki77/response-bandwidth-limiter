# Response Bandwidth Limiter

*他の言語で読む: [English](README.md), [日本語](README.ja.md)*

Response Bandwidth Limiter は、FastAPI と Starlette に対してエンドポイント単位のレスポンス帯域制限と、クライアント単位の request count policy を適用するミドルウェア統合ライブラリです。

## インストール

```bash
pip install response-bandwidth-limiter
```

利用するフレームワークも合わせてインストールしてください。

```bash
pip install fastapi
# または
pip install starlette
```

開発やテスト用の依存関係を含める場合:

```bash
pip install response-bandwidth-limiter[dev]
```

## 基本的な使い方

### FastAPI

```python
from fastapi import FastAPI, Request
from starlette.responses import FileResponse

from response_bandwidth_limiter import ResponseBandwidthLimiter

app = FastAPI()
limiter = ResponseBandwidthLimiter()

@app.get("/download")
@limiter.limit(1024)
async def download_file(request: Request):
    return FileResponse("path/to/large_file.txt")

@app.get("/video")
@limiter.limit(2048)
async def stream_video(request: Request):
    return FileResponse("path/to/video.mp4")

limiter.init_app(app)
```

`init_app()` が正式な登録方法です。middleware の追加と `app.state` への保持をまとめて行います。

### `limit_rules` による request count policy

```python
from fastapi import FastAPI, Request
from starlette.responses import PlainTextResponse

from response_bandwidth_limiter import Delay, Reject, ResponseBandwidthLimiter, Rule, Throttle

app = FastAPI()
limiter = ResponseBandwidthLimiter()

@app.get("/download")
@limiter.limit_rules([
    Rule(count=10, per="second", action=Throttle(bytes_per_sec=512)),
    Rule(count=30, per="minute", action=Delay(seconds=0.5)),
    Rule(count=200, per="hour", action=Reject(detail="同一IPからのリクエストが多すぎます")),
])
async def download_file(request: Request):
    return PlainTextResponse("payload" * 4096)

limiter.init_app(app)
```

利用できる action:

1. `Throttle(bytes_per_sec=...)`: レスポンスストリームを低速化します。
2. `Delay(seconds=...)`: エンドポイント実行前に待機します。
3. `Reject(status_code=429, detail=...)`: エラー応答を返します。

### Starlette

```python
from starlette.applications import Starlette
from starlette.responses import FileResponse
from starlette.routing import Route

from response_bandwidth_limiter import ResponseBandwidthLimiter

limiter = ResponseBandwidthLimiter()

async def download_file(request):
    return FileResponse("path/to/large_file.txt")

routes = [
    Route("/download", endpoint=limiter.limit(1024)(download_file)),
]

app = Starlette(routes=routes)
limiter.init_app(app)
```

## 実行時の設定更新

設定はすべて limiter が所有します。辞書を直接変更せず、専用メソッドを使って更新してください。

### 帯域制限の更新

```python
@app.get("/admin/set-limit")
async def set_limit(endpoint: str, limit: int):
    limiter.update_route(endpoint, limit)
    return {"status": "success", "endpoint": endpoint, "limit": limit}
```

### policy の更新

```python
from response_bandwidth_limiter import Delay, Reject, Rule, Throttle

@app.get("/admin/set-policy")
async def set_policy(endpoint: str, mode: str):
    if mode == "throttle":
        limiter.update_policy(endpoint, [
            Rule(count=5, per="second", action=Throttle(bytes_per_sec=256)),
            Rule(count=20, per="minute", action=Reject(detail="リクエストが多すぎます")),
        ])
    elif mode == "delay":
        limiter.update_policy(endpoint, [
            Rule(count=3, per="second", action=Delay(seconds=0.25)),
        ])
    else:
        limiter.remove_policy(endpoint)

    return {"status": "success", "endpoint": endpoint}
```

上記の `/admin` エンドポイントは説明用の最小サンプルです。本番環境では通常の認証・認可を必ず追加してください。

実行可能なサンプルは [example/main.py](example/main.py) と [example/dynamic_limit_example.py](example/dynamic_limit_example.py) を参照してください。

## 制限事項と注意点

- 帯域制限はサーバーサイドで適用されるため、実際の転送速度はネットワーク状況にも依存します。
- request count policy はメモリ上で管理されます。分散構成ではプロセス間・サーバー間で共有されません。
- `X-Forwarded-For` を識別に使う場合は、信頼できるリバースプロキシの背後でのみその値を信用してください。
- 不正な proxy header 値は無視され、middleware は直接接続元のアドレスへフォールバックします。

## APIリファレンス

### `ResponseBandwidthLimiter`

```python
class ResponseBandwidthLimiter:
    def __init__(self, key_func=None, trusted_proxy_headers: bool = False): ...
    def limit(self, rate: int): ...
    def limit_rules(self, rules: list[Rule]): ...
    def init_app(self, app): ...
    def update_route(self, endpoint_name: str, rate: int): ...
    def remove_route(self, endpoint_name: str): ...
    def update_policy(self, endpoint_name: str, rules: list[Rule]): ...
    def remove_policy(self, endpoint_name: str): ...
    def get_limit(self, endpoint_name: str) -> int | None: ...
    def get_rules(self, endpoint_name: str) -> list[Rule]: ...
    @property
    def routes(self) -> Mapping[str, int]: ...
    @property
    def policies(self) -> Mapping[str, list[Rule]]: ...
    @property
    def configured_names(self) -> set[str]: ...
```

`key_func` を指定すると、request count policy で使うクライアント識別子を独自に決められます。
`trusted_proxy_headers` の既定値は `False` です。`X-Forwarded-For` や `X-Real-IP` を信頼できるリバースプロキシ配下でのみ `True` にしてください。
デコレータは limiter の設定だけを登録し、エンドポイントの元のシグネチャは保持されます。

- `routes` は現在設定されている帯域制限を返します。
- `policies` は現在設定されている request count rule を返します。
- `configured_names` は route と policy の両方で設定済みの名前集合を返します。

### `Rule`, `Throttle`, `Delay`, `Reject`

```python
Rule(count: int, per: str, action, scope: str = "ip")
Throttle(bytes_per_sec: int)
Delay(seconds: float)
Reject(status_code: int = 429, detail: str = "Rate limit exceeded")
```

- `per` は `second`、`minute`、`hour` をサポートします。
- `scope` は現状 `ip` のみです。
- Action には `priority` と `to_dict()` があります。

独自 action を追加する場合は `ActionProtocol` を実装し、`decide()` から `PolicyDecision` を返してください。

`ActionProtocol` には次のメンバーが必要です。

- `priority: int`
- `sort_key: int | float`
- `to_dict() -> dict[str, Any]`
- `decide(retry_after: int) -> PolicyDecision`

`Action` も `ActionProtocol` の型エイリアスとして公開されています。

`PolicyDecision` には、rule が一致したときに middleware が使う次のフィールドがあります。

- `reject`: 直ちにエラーレスポンスを返すかどうか。
- `reject_status`: reject 時に使う HTTP ステータスコード。
- `reject_detail`: JSON ボディに返す詳細メッセージ。
- `retry_after`: `Retry-After` ヘッダーに書き込まれる値。
- `pre_delay`: エンドポイント実行前に適用される待機時間。
- `throttle_rate`: レスポンスに一時的に適用される bytes-per-second 制限値。

### `ResponseBandwidthLimiterMiddleware`

実際に帯域制限と policy を適用する middleware です。通常は手動で追加せず、`limiter.init_app(app)` を使ってください。

### ユーティリティ関数

```python
def get_endpoint_name(request):
    """
    リクエストからエンドポイント名を取得します
    
    引数:
        request: リクエストオブジェクト
    
    戻り値:
        str: エンドポイント名
    """
    
def get_route_path(request):
    """
    リクエストからルートパスを取得します
    
    引数:
        request: リクエストオブジェクト
        
    戻り値:
        str: ルートパス
    """
```

## ソースコード

このライブラリのソースコードは以下のGitHubリポジトリで公開されています：
https://github.com/kirisaki77/response-bandwidth-limiter

## 謝辞

このライブラリは [slowapi](https://github.com/laurentS/slowapi) (MIT Licensed) にインスパイアされました。

## ライセンス

MPL-2.0

## PyPI

https://pypi.org/project/response-bandwidth-limiter/
