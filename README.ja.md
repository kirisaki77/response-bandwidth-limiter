# Response Bandwidth Limiter

*他の言語で読む: [English](README.md), [日本語](README.ja.md)*

FastAPIとStarlette用のレスポンス帯域制限ミドルウェアです。特定のエンドポイントのレスポンス送信速度を制限でき、さらに IP ごとの request count に応じた段階的な policy も適用できます。

## インストール

pipを使用してインストールできます：

```bash
pip install response-bandwidth-limiter
```

### 依存関係

このライブラリは最小限の依存関係で動作しますが、実際の使用にはFastAPIまたはStarletteが必要です。
必要に応じて以下のようにインストールしてください：

```bash
# FastAPIと一緒に使用する場合
pip install fastapi

# Starletteと一緒に使用する場合
pip install starlette

# 開発やテストに必要な依存関係を含める場合
pip install response-bandwidth-limiter[dev]
```

## 基本的な使い方

### デコレータを使った方法（推奨）

```python
from fastapi import FastAPI, Request
from starlette.responses import FileResponse
from response_bandwidth_limiter import ResponseBandwidthLimiter, ResponseBandwidthLimitExceeded, _response_bandwidth_limit_exceeded_handler

# リミッターの初期化
limiter = ResponseBandwidthLimiter()
app = FastAPI()

# アプリケーションに登録
app.state.response_bandwidth_limiter = limiter
app.add_exception_handler(ResponseBandwidthLimitExceeded, _response_bandwidth_limit_exceeded_handler)

# エンドポイントのレスポンス帯域制限（1024 bytes/sec）
@app.get("/download")
@limiter.limit(1024)  # 1024 bytes/sec
async def download_file(request: Request):
    return FileResponse("path/to/large_file.txt")

# 別のエンドポイントに別の制限（2048 bytes/sec）
@app.get("/video")
@limiter.limit(2048)  # 2048 bytes/sec
async def stream_video(request: Request):
    return FileResponse("path/to/video.mp4")
```

### `limit_rules` による request count policy

IP ごとの request count に応じて、段階的に throttle、delay、reject を適用できます。各 rule は一定時間内の回数を監視し、条件を超えたときに 1 つの action を発動します。

```python
from fastapi import FastAPI, Request
from starlette.responses import PlainTextResponse
from response_bandwidth_limiter import (
    Delay,
    Reject,
    ResponseBandwidthLimiter,
    ResponseBandwidthLimiterMiddleware,
    Rule,
    Throttle,
)

app = FastAPI()
limiter = ResponseBandwidthLimiter()
app.state.response_bandwidth_limiter = limiter
app.add_middleware(ResponseBandwidthLimiterMiddleware)

@app.get("/download")
@limiter.limit_rules([
    Rule(count=10, per="second", action=Throttle(bytes_per_sec=512)),
    Rule(count=30, per="minute", action=Delay(seconds=0.5)),
    Rule(count=200, per="hour", action=Reject(detail="同一IPからのリクエストが多すぎます")),
])
async def download_file(request: Request):
    return PlainTextResponse("payload" * 4096)
```

利用できる action:

1. `Throttle(bytes_per_sec=...)`: レスポンスストリームを低速化します。
2. `Delay(seconds=...)`: エンドポイント実行前に待機します。
3. `Reject(status_code=429, detail=...)`: エラー応答を返します。

### Starletteでの使用例

```python
from starlette.applications import Starlette
from starlette.responses import FileResponse
from starlette.routing import Route
from response_bandwidth_limiter import ResponseBandwidthLimiter

# デコレータ方式
limiter = ResponseBandwidthLimiter()

async def download_file(request):
    return FileResponse("path/to/large_file.txt")

# デコレータを適用
download_with_limit = limiter.limit(1024)(download_file)

# ルートを定義
routes = [
    Route("/download", endpoint=download_with_limit)
]

app = Starlette(routes=routes)

# リミッターをアプリに登録
limiter.init_app(app)
```

## 高度な使用例

### デコレータを使った帯域制限の設定（シンプルなケース）

シンプルに帯域制限を設定する場合は、`set_response_bandwidth_limit`デコレータを使用できます：

```python
from fastapi import FastAPI
from starlette.responses import FileResponse
from response_bandwidth_limiter import set_response_bandwidth_limit

app = FastAPI()

@app.get("/download")
@set_response_bandwidth_limit(1024)  # 1024 bytes/sec
async def download_file():
    return FileResponse("path/to/large_file.txt")
```

この方法では、`ResponseBandwidthLimiter`クラスを初期化せずに、直接エンドポイントに帯域制限を設定できます。
さらに、このデコレータを使用する場合は、ミドルウェアを明示的に追加する必要があります：

```python
from response_bandwidth_limiter import ResponseBandwidthLimiterMiddleware

app = FastAPI()
app.add_middleware(ResponseBandwidthLimiterMiddleware)

@app.get("/download")
@set_response_bandwidth_limit(1024)
async def download_file():
    return FileResponse("path/to/large_file.txt")
```

このシンプルなデコレータはグローバルな設定を使用するため、複数のアプリケーションで同じ関数名を使用する場合は注意してください。より複雑なシナリオでは、`ResponseBandwidthLimiter`クラスを使用するアプローチが推奨されます。

### シンプルデコレータと標準デコレータの違い

シンプルデコレータ (`set_response_bandwidth_limit`) と標準デコレータ (`ResponseBandwidthLimiter.limit`) の主な違い：

1. シンプルデコレータ:
   - グローバルな設定を使用
   - アプリインスタンスに依存しない
   - 複数アプリで同名の関数を使うと競合する可能性あり
   - 設定が簡単

2. 標準デコレータ:
   - アプリインスタンスごとに分離された設定
   - 複数のアプリで安全に使用可能
   - より明示的な初期化が必要
   - 大規模アプリに適している

### 両方のデコレータを併用する

同じアプリ内で両方のデコレータを使用することもできます：

```python
from fastapi import FastAPI, Request
from response_bandwidth_limiter import (
    ResponseBandwidthLimiter,
    set_response_bandwidth_limit,
    ResponseBandwidthLimiterMiddleware
)

app = FastAPI()
limiter = ResponseBandwidthLimiter()
app.state.response_bandwidth_limiter = limiter

# ミドルウェアは一度だけ追加
app.add_middleware(ResponseBandwidthLimiterMiddleware)

# 標準デコレータの使用例
@app.get("/video")
@limiter.limit(2048)
async def stream_video(request: Request):
    # ...

# シンプルデコレータの使用例
@app.get("/download")
@set_response_bandwidth_limit(1024)
async def download_file(request: Request):
    # ...
```

### 動的な帯域制限

実行時に帯域制限を変更したい場合：

```python
limiter = ResponseBandwidthLimiter()
app = FastAPI()
app.state.response_bandwidth_limiter = limiter

@app.get("/admin/set-limit")
async def set_limit(endpoint: str, limit: int):
    limiter.routes[endpoint] = limit
    return {"status": "success", "endpoint": endpoint, "limit": limit}
```

上記の `/admin` エンドポイントは説明用の最小サンプルであり、認証・認可を含んでいません。本番環境でそのまま公開せず、実行時設定を変更できるエンドポイントには通常のアクセス制御を必ず追加してください。

**重要な注意点**: 帯域制限の変更は永続的です。一度エンドポイントの帯域制限を変更すると、その変更はサーバーが再起動されるまで保持され、次回以降のすべてのリクエストに適用されます。一時的な変更ではなく、設定の更新として扱われます。

例えば、あるエンドポイントの制限を1000 bytes/secから2000 bytes/secに変更した場合、それ以降のすべてのリクエストは2000 bytes/secの制限で処理されます。元の速度に戻す場合は、明示的に再設定する必要があります。

### 動的な policy 更新

`limiter.policies` を更新することで、request count policy も実行時に差し替えできます。

```python
from response_bandwidth_limiter import Delay, Reject, Rule, Throttle

@app.get("/admin/set-policy")
async def set_policy(endpoint: str, mode: str):
    if mode == "throttle":
        limiter.policies[endpoint] = [
            Rule(count=5, per="second", action=Throttle(bytes_per_sec=256)),
            Rule(count=20, per="minute", action=Reject(detail="リクエストが多すぎます")),
        ]
    elif mode == "delay":
        limiter.policies[endpoint] = [
            Rule(count=3, per="second", action=Delay(seconds=0.25)),
        ]
    else:
        limiter.policies.pop(endpoint, None)

    return {"status": "success", "endpoint": endpoint, "policies": endpoint in limiter.policies}
```

`limiter.routes` と同様に、policy の変更も次回以降のリクエストに継続して適用されます。

`/admin/set-policy` も同様に管理用サンプルです。ローカル開発や内部運用以外で同種のエンドポイントを使う場合は、認証・認可を追加してください。

### 特定のユーザーやIPに対する帯域制限

```python
@app.get("/download/{user_id}")
@limiter.limit(1024)
async def download_for_user(request: Request, user_id: str):
    # ユーザーごとに異なる制限を適用したい場合は、
    # ここでカスタム処理を行うことができます
    user_limits = {
        "premium": 5120,
        "basic": 1024
    }
    user_type = get_user_type(user_id)
    actual_limit = user_limits.get(user_type, 512)
    # ...レスポンス処理
```

## 制限事項と注意点

- 帯域制限はサーバーサイドで適用されるため、クライアント側の帯域幅やネットワーク状況によっては、実際の転送速度が変わる場合があります。
- 大きなファイル転送の場合は、メモリ使用量に注意してください。
- 分散システムの場合、各サーバーごとに制限が適用されます。
- `X-Forwarded-For` をリクエスト識別に使う場合は、そのヘッダーを上書きまたはサニタイズする信頼済みリバースプロキシの背後でのみ信頼してください。そうでない場合、クライアントがヘッダーを偽装して IP ベースの policy を回避できます。

## APIリファレンス

このセクションでは、ライブラリが提供する主なクラスとメソッドの詳細なリファレンスを提供します。

### ResponseBandwidthLimiter

レスポンス帯域制限の機能を提供するメインクラスです。

```python
class ResponseBandwidthLimiter:
    def __init__(self, key_func=None):
        """
        レスポンス帯域幅制限機能を初期化します
        
        引数:
            key_func: 将来的な拡張用のキー関数（現在は使用されていません）
        """
        
    def limit(self, rate: int):
        """
        エンドポイントに対して帯域制限を適用するデコレータを返します
        
        引数:
            rate: 制限する速度（bytes/sec）
            
        戻り値:
            デコレータ関数
            
        例外:
            TypeError: rateが整数でない場合
        """

    def limit_rules(self, rules):
        """
        request count ベースの rule をエンドポイントに適用するデコレータを返します

        引数:
            rules: リクエストごとに評価される Rule の配列

        戻り値:
            デコレータ関数
        """
        
    def init_app(self, app):
        """
        FastAPIまたはStarletteアプリケーションにリミッターを登録します
        
        引数:
            app: FastAPIまたはStarletteアプリケーションインスタンス
        """
```

### Rule, Throttle, Delay, Reject

```python
Rule(count: int, per: str, action, scope: str = "ip")
Throttle(bytes_per_sec: int)
Delay(seconds: float)
Reject(status_code: int = 429, detail: str = "Rate limit exceeded")
```

- `per` は `second`、`minute`、`hour` をサポートします。
- `scope` は現状 `ip` のみサポートします。
- rule は endpoint ごと、かつ client IP ごとに評価されます。

### ResponseBandwidthLimiterMiddleware

FastAPIおよびStarlette用のミドルウェアで、帯域制限を実際に適用します。

```python
class ResponseBandwidthLimiterMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        """
        帯域制限ミドルウェアを初期化します
        
        引数:
            app: FastAPIまたはStarletteアプリケーション
        """
        
    def get_handler_name(self, request, path):
        """
        パスに一致するハンドラー名を取得します
        
        引数:
            request: リクエストオブジェクト
            path: リクエストパス
            
        戻り値:
            str または None: エンドポイント名（存在する場合）
        """
        
    async def dispatch(self, request, call_next):
        """
        リクエストに対して帯域制限を適用します
        
        引数:
            request: リクエストオブジェクト
            call_next: 次のミドルウェア関数
            
        戻り値:
            レスポンスオブジェクト
        """
```

### set_response_bandwidth_limit

シンプルな帯域制限デコレータです。

```python
def set_response_bandwidth_limit(limit: int):
    """
    エンドポイントごとに帯域制限を設定するシンプルなデコレータ
    
    引数:
        limit: 制限する速度（bytes/sec）
        
    戻り値:
        デコレータ関数
    """
```

### ResponseBandwidthLimitExceeded

帯域制限超過時に発生する例外です。

```python
class ResponseBandwidthLimitExceeded(Exception):
    """
    帯域幅の制限を超過した場合に発生する例外
    
    引数:
        limit: 制限値（bytes/sec）
        endpoint: 制限が適用されたエンドポイント名
    """
```

### エラーハンドラ

```python
async def _response_bandwidth_limit_exceeded_handler(request, exc):
    """
    帯域幅制限超過時のエラーハンドラー
    
    引数:
        request: リクエストオブジェクト
        exc: ResponseBandwidthLimitExceeded例外
        
    戻り値:
        JSONResponse: HTTPステータスコード429と説明
    """
```

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
