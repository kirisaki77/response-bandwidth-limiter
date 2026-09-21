# Response Bandwidth Limiter

*他の言語で読む: [English](README.md), [日本語](README.ja.md)*

Response Bandwidth Limiter は、FastAPIとStarletteでエンドポイントごとに転送速度の上限を設定するライブラリです。上限は各レスポンスに独立して適用されます。

リクエスト数に応じた拒否・遅延・帯域制限にも対応しています。IPアドレス・APIキー・ユーザーなどの単位で集計でき、Redisを使えば複数のワーカーでカウンタを共有できます。

## インストール

Python 3.10以上が必要です。以下のFastAPIの例を動かすには、ライブラリ、FastAPI、サーバーのUvicornをインストールしてください。

```bash
python -m pip install response-bandwidth-limiter fastapi uvicorn
```

Starletteでの使い方は[インストールと基本的な使い方](docs/usage.ja.md)を参照してください。

## 最小の使用例

次のコードを `main.py` として保存します。

```python
from fastapi import FastAPI, Request
from starlette.responses import PlainTextResponse

from response_bandwidth_limiter import ResponseBandwidthLimiter

app = FastAPI()
limiter = ResponseBandwidthLimiter()

@app.get("/download")
@limiter.limit(1024)  # 1024 バイト/秒
async def download(request: Request):
    return PlainTextResponse("payload" * 4096)

limiter.init_app(app)
```

ルートを定義した後に `limiter.init_app(app)` を呼び出して、ミドルウェアを登録します。`/download` の各レスポンスは1,024バイト/秒に制限されます。同時に複数のレスポンスを返す場合も、それぞれに独立した上限が適用されます。

`main.py` を保存したディレクトリでサーバーを起動します。

```bash
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

ブラウザーで [http://127.0.0.1:8000/download](http://127.0.0.1:8000/download) を開きます。`payload` を繰り返した28 KiBのテキストが返り、設定した速度では転送完了まで約28秒かかります。この待ち時間は帯域制限によるものです。

## 主な注意点

- リクエスト数のカウンタとIPの許可・拒否情報は、既定ではプロセスごとに保存されます。Redisを使っても、実行時の設定更新はプロセスごとに必要です。
- `trusted_proxy_headers` は、クライアントIPヘッダーを上書き・検証する信頼できるリバースプロキシの背後でのみ有効にしてください。
- `init_app()` は既定で終了シグナルを処理します。アプリケーション側で終了処理を管理する場合は `install_signal_handlers=False` を指定してください。詳しくは[終了時の動作](docs/usage.ja.md#終了時の動作)を参照してください。
- 実際の転送速度はネットワーク状況にも依存します。

## 詳細ドキュメント

| 目的 | 内容 |
| --- | --- |
| [インストールと基本的な使い方](docs/usage.ja.md) | FastAPI・Starletteの例、追加の依存関係、終了時の動作 |
| [リクエスト数の制限とスコープ](docs/policies.ja.md) | `limit_rules`、アクションの優先順位、IP・APIキー・ユーザー単位の集計 |
| [Redis・運用設定・移行](docs/operations.ja.md) | カウンタ共有、実行時更新、エンドポイントの識別子、制限事項、`key_func` からの移行 |
| [APIリファレンス](docs/api-reference.ja.md) | 公開メソッド、ストレージ、ルール、独自アクション |
| [開発と配布物の検証](docs/development.ja.md) | 互換性テスト、wheelの検証、リリース手順 |

実行可能なサンプルは [example/](example/) にあります。

## プロジェクト

- [ソースコード](https://github.com/kirisaki77/response-bandwidth-limiter)
- [PyPI](https://pypi.org/project/response-bandwidth-limiter/)

## 謝辞

このライブラリは、MITライセンスで公開されている [slowapi](https://github.com/laurentS/slowapi) に着想を得ています。

## ライセンス

[MPL-2.0](LICENSE)
