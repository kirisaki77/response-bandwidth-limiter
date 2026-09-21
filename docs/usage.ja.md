# インストールと基本的な使い方

[READMEに戻る](../README.ja.md) | [English](usage.md) | [日本語](usage.ja.md)

## インストール

Python 3.10以上が必要です。ライブラリと、利用するフレームワークをインストールしてください。

```bash
pip install response-bandwidth-limiter
```

```bash
pip install fastapi
# または
pip install starlette
```

Redisでリクエスト数のカウンタを共有する場合は、追加の依存関係をインストールします。接続設定は[Redis・運用設定・移行](operations.ja.md)を参照してください。

```bash
pip install response-bandwidth-limiter[redis]
```

## FastAPI

帯域制限は `@limiter.limit()` にバイト/秒で指定します。以下の例では、ダウンロードを1,024バイト/秒、動画を2,048バイト/秒に制限します。ファイルパスは実際のファイルに置き換えてください。

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

ルートを定義した後に `limiter.init_app(app)` を呼び出します。このメソッドはミドルウェアを追加し、limiterを `app.state` に保存します。

## Starlette

Starletteでは、制限を設定した関数を `Route` のエンドポイントに指定します。

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

## 終了時の動作

`init_app()` は既定で `SIGINT` の処理を登録します。

1. 1回目の `Ctrl+C` でdrainモードに入ります。帯域制限またはリクエスト数制限を設定したルートへの新規リクエストを `503` で拒否し、進行中の帯域制限ストリーミング応答は継続します。
2. 2回目の `Ctrl+C` でabortモードに切り替わり、帯域制限中の送信を中断します。

アプリケーション側で終了処理を管理する場合は、`init_app(app, install_signal_handlers=False)` を指定してください。

---

[リクエスト数の制限とスコープ](policies.ja.md) · [Redis・運用設定・移行](operations.ja.md) · [APIリファレンス](api-reference.ja.md) · [開発と配布物の検証](development.ja.md)
