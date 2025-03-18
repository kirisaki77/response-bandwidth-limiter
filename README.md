# Bandwidth Limiter

FastAPIとStarlette用の帯域制限（レスポンス速度制限）ミドルウェア。特定のエンドポイントのレスポンス送信速度を制限することができます。

## インストール

pipを使用してインストールできます：

```bash
pip install bandwidth-limiter
```

## 基本的な使い方

### デコレータを使った方法（推奨）

```python
from fastapi import FastAPI, Request
from starlette.responses import FileResponse
from bandwidth_limiter import BandwidthLimiter, BandwidthLimitExceeded, _bandwidth_limit_exceeded_handler

# リミッターの初期化
limiter = BandwidthLimiter()
app = FastAPI()

# アプリケーションに登録
app.state.bandwidth_limiter = limiter
app.add_exception_handler(BandwidthLimitExceeded, _bandwidth_limit_exceeded_handler)

# エンドポイントの帯域制限（1024 bytes/sec）
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

### ミドルウェアを直接使う方法

```python
from fastapi import FastAPI
from bandwidth_limiter import BandwidthLimiterMiddleware

app = FastAPI()

# エンドポイント名とbytes/secの対応を指定
app.add_middleware(
    BandwidthLimiterMiddleware, 
    limits={
        "download_file": 1024,  # 1024 bytes/sec
        "stream_video": 2048,   # 2048 bytes/sec
    }
)

@app.get("/download")
async def download_file():
    return FileResponse("path/to/large_file.txt")

@app.get("/video")
async def stream_video():
    return FileResponse("path/to/video.mp4")
```

### Starletteでの使用例

```python
from starlette.applications import Starlette
from starlette.responses import FileResponse
from starlette.routing import Route
from bandwidth_limiter import BandwidthLimiter, BandwidthLimiterMiddleware

# デコレータ方式
limiter = BandwidthLimiter()

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

# または直接ミドルウェアを使用
app.add_middleware(
    BandwidthLimiterMiddleware, 
    limits={"download_file": 1024}
)
```

## APIリファレンス

### BandwidthLimiter

帯域幅制限装飾子を提供するクラス。

```python
from bandwidth_limiter import BandwidthLimiter

limiter = BandwidthLimiter()
```

#### メソッド

- **limit(rate: int) -> Callable**  
  帯域幅を制限するデコレータ。bytes/secの整数値を指定します。

- **init_app(app: Union[FastAPI, Starlette]) -> None**  
  アプリケーションにリミッターを登録します。

### BandwidthLimiterMiddleware

帯域制限を適用するミドルウェア。

```python
from bandwidth_limiter import BandwidthLimiterMiddleware

app.add_middleware(
    BandwidthLimiterMiddleware, 
    limits={"endpoint_name": rate_in_bytes_per_sec}
)
```

### 例外処理

帯域制限超過時に例外を発生させる場合は、例外ハンドラーを登録してください。

```python
from bandwidth_limiter import BandwidthLimitExceeded, _bandwidth_limit_exceeded_handler

app.add_exception_handler(BandwidthLimitExceeded, _bandwidth_limit_exceeded_handler)
```

## 高度な使用例

### 動的な帯域制限

実行時に帯域制限を変更したい場合：

```python
limiter = BandwidthLimiter()
app = FastAPI()
app.state.bandwidth_limiter = limiter

@app.get("/admin/set-limit")
async def set_limit(endpoint: str, limit: int):
    limiter.routes[endpoint] = limit
    return {"status": "success", "endpoint": endpoint, "limit": limit}
```

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

## ライセンス

MIT
