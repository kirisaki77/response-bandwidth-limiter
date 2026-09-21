# 開発と配布物の検証

[READMEに戻る](../README.ja.md) | [English](development.md) | [日本語](development.ja.md)

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
