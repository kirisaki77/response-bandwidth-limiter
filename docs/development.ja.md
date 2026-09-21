# 開発と配布物の検証

[READMEに戻る](../README.ja.md) | [English](development.md) | [日本語](development.ja.md)

## ソースコードの構成

- `limiter.py`: 公開API、設定、デコレータ、アプリへの登録。
- `middleware.py`: ASGIリクエストの判定順序とレスポンス送信。
- `routing.py`: ルート探索とエンドポイント識別子。
- `identity.py`: クライアントIPとポリシースコープの解決。
- `signals.py`: SIGINTの登録・復元。終了状態は `shutdown.py` が管理。
- `models.py` / `policy.py`: ルール・アクションの型とポリシー評価。
- `streaming.py`: 帯域制限付きのチャンク送信。
- `backends/`: ストレージの基底型とメモリ・Manager・Redis実装。

`storage.py`、`redis_storage.py`、`util.py` は既存のimportを維持する互換モジュールです。ストレージの運用上の警告は `storage.py` に残しています。Redis実装は明示的に参照されたときに読み込まれ、通常のパッケージimportではRedisの依存関係を必要としません。

## 開発用の依存関係

開発・テスト用の追加依存関係をインストールするには、次のコマンドを実行します。

```bash
pip install response-bandwidth-limiter[dev]
```

## 互換性テスト

CIでは、バージョンを固定した開発用の依存関係を使い、WindowsとLinuxのPython 3.10・3.14で全テストを実行します。最低対応バージョンのStarlette 0.20.0は、両Pythonバージョンで別途検証します。

FastAPIやHTTPテストクライアントに依存しないASGIテストでは、次の動作を確認します。

- 帯域制限とリクエスト数による拒否
- マウントされた動的ルートと実行時のポリシー削除
- ファイル応答とストリーミング応答
- アプリケーション終了時のリソース解放

帯域制限の回帰テストでは、各チャンクが自身の待機時間の直後に送信されることを確認します。初回の送信時刻、送信間隔、最後のASGIボディのフラグも検証します。

## 実HTTPのE2Eテスト

`tests/e2e/` はUvicornを別プロセスで起動し、ループバックHTTP通信で帯域制限、ファイル・ストリーム応答、429・遅延、クライアント切断後の処理解放、drain・abortを検証します。Linuxでは実SIGINTによる処理完了とストレージの解放も確認します。Redisテストは異なる2プロセスでカウンタとIP拒否情報を共有します。

サーバーは `python -I` で起動するため、変更後のパッケージを事前にインストールしてください。

```console
python -m pip install -r requirements/dev.txt
python -m pip install .
python -m pytest tests/e2e -v -ra
```

Redisテストにはテスト用Redisの `REDIS_URL` を設定します。テスト固有のキープレフィックスを使い、共有DBの全削除は行いません。WindowsのSIGINTテストとRedis未設定時のRedisテストはスキップされます。CIの専用LinuxジョブはPython 3.10・3.14とRedis 7を使い、スキップがあれば失敗します。JUnit結果とサーバーログはActionsの成果物として保存されます。

通常のテストだけを実行する場合は `python -m pytest -q --ignore=tests/e2e` を使用します。

## 配布物の検証

CIとリリース用ワークフローでは、生成したwheelを新しい仮想環境にインストールし、ソースツリーを読み込まない隔離モードで同じASGIテストを実行します。

ローカルでは次のコマンドで確認できます。`dist` には検証対象のwheelを1つだけ置いてください。

```console
python -m build
python scripts/verify_wheel.py dist
```

実行時の依存関係をインストールするため、パッケージインデックスへのアクセスが必要です。

## リリース

公開手順は[リリース手順](../RELEASING.md)を参照してください。

---

[インストールと基本的な使い方](usage.ja.md) · [リクエスト数の制限とスコープ](policies.ja.md) · [Redis・運用設定・移行](operations.ja.md) · [APIリファレンス](api-reference.ja.md)
