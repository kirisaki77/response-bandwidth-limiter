# APIリファレンス

[READMEに戻る](../README.ja.md) | [English](api-reference.md) | [日本語](api-reference.ja.md)

## `ResponseBandwidthLimiter`

```python
class ResponseBandwidthLimiter:
    def __init__(self, trusted_proxy_headers: bool = False, storage: Storage | None = None): ...
    def register_scope_resolver(self, scope_name: str, resolver: ScopeResolver): ...
    def scope_resolvers(self) -> Mapping[str, ScopeResolver]: ...  # property
    def resolve_handler_identifier(self, request: Request) -> str | None: ...
    def limit(self, rate: int): ...
    def limit_rules(self, rules: list[Rule]): ...
    def init_app(self, app, install_signal_handlers: bool = True): ...
    def begin_shutdown(self, mode: ShutdownMode): ...
    async def shutdown(self, mode: ShutdownMode, timeout: float | None = None) -> bool: ...
    async def close(self) -> None: ...
    async def block_ip(self, ip: str, duration: int | None = None) -> None: ...
    async def unblock_ip(self, ip: str) -> None: ...
    async def is_blocked(self, ip: str) -> bool: ...
    async def allow_ip(self, ip: str) -> None: ...
    async def remove_allow(self, ip: str) -> None: ...
    async def is_allowed(self, ip: str) -> bool: ...
    def update_route(self, endpoint_name: str, rate: int): ...
    def remove_route(self, endpoint_name: str): ...
    def update_policy(self, endpoint_name: str, rules: list[Rule]): ...
    def remove_policy(self, endpoint_name: str): ...
    def get_limit(self, endpoint_name: str) -> int | None: ...
    def get_rules(self, endpoint_name: str) -> list[Rule]: ...
    @property
    def shutdown_coordinator(self) -> ShutdownCoordinator: ...
    @property
    def storage(self) -> Storage: ...
    @property
    def ip_manager(self) -> IPManager: ...
    @property
    def routes(self) -> Mapping[str, int]: ...
    @property
    def policies(self) -> Mapping[str, list[Rule]]: ...
    @property
    def configured_names(self) -> set[str]: ...
```

`trusted_proxy_headers` の既定値は `False` です。有効にする条件は[プロキシ経由のIPアドレス](operations.ja.md#プロキシ経由のipアドレス)を参照してください。`storage` はリクエスト数のカウンタとIPの許可・拒否情報の保存先で、省略時は `InMemoryStorage` を使います。

デコレータは設定を登録し、エンドポイントの元のシグネチャを保持します。登録手順と終了時の動作は[基本的な使い方](usage.ja.md)、設定の更新と識別子の解決順序は[運用設定](operations.ja.md)を参照してください。

### スコープの登録

`register_scope_resolver(scope_name, resolver)` は、リクエストから集計用の識別子を返す同期関数を登録します。

- スコープを使うルールは、登録後に `limit_rules()` または `update_policy()` で設定してください。
- スコープ名の前後の空白は検証時に除去されます。
- 同じ名前を重複して登録することはできません。
- 組み込みの `ip` と `default` は予約済みで、上書きできません。

### 設定と状態の参照

| プロパティ | 内容 |
| --- | --- |
| `scope_resolvers` | 登録済みスコープ名とリゾルバーの読み取り専用マッピング |
| `routes` | 設定済みの帯域制限 |
| `policies` | 設定済みのリクエスト数制限ルール |
| `configured_names` | 帯域制限またはリクエスト数制限が設定された名前の集合 |
| `storage` | 使用中の `Storage` インスタンス |
| `ip_manager` | 使用中の `IPManager` インスタンス |
| `shutdown_coordinator` | 終了処理を管理する `ShutdownCoordinator` インスタンス |

## ストレージ

各ストレージは `Storage` を継承します。

`InMemoryStorage(max_keys=10000)` は容量超過時に通常の値を削除しますが、有効な `ip:` 制御データは保持します。全枠がIP制御データで埋まると、新しいキーの追加は `StorageUnavailableError` になります。既存データの更新・削除は可能です。大きな許可・拒否リストを扱う場合は容量を増やすか、Redisを使用してください。

`ManagerStorage` は書き込み時に、インスタンスごとに最大1秒に1回、期限切れの値を回収します。再参照されない古いリクエストカウンタも対象です。非同期の読み書きではManagerとの同期通信をワーカースレッドで実行します。待機タスクをキャンセルしても、実行中の書き込みは停止しません。この実装は引き続き実験的なもので、高負荷の用途には適していません。

独自の `record_hit()` 実装は `SlidingWindowResult` を返します。任意の `retry_after_timestamp` は、次のリクエストを受け入れるために期限切れになる必要がある履歴の時刻です。評価器はこの値にルールの期間を加えて `Retry-After` を計算します。省略時は従来どおり `oldest_timestamp` を使用します。

| クラス | 用途と制約 |
| --- | --- |
| `InMemoryStorage` | プロセス内で厳密なスライディングウィンドウ集計を行う既定の実装 |
| `ManagerStorage` | `multiprocessing.Manager` を使う実験的な共有実装。厳密なスライディングウィンドウの動作は保証しない |
| `RedisStorage` | ワーカー間・サーバー間でカウンタを共有する実装。Redisサーバー5.0以上が必要 |

`RedisStorage.from_url("redis://...")` でRedisに接続するストレージを作成します。主なオプションは次のとおりです。

- `counter_failure_mode`: `"open"`、`"closed"`、`"local-memory-fallback"` を指定できます。
- `control_failure_mode`: `"closed"`、`"local-memory-fallback"` を指定できます。
- `key_hash=True`: Redisキーのうち、リクエスト識別子の部分だけをハッシュ化します。

接続例と運用上の制約は[Redis・運用設定・移行](operations.ja.md)を参照してください。

## `Rule` と組み込みアクション

```python
Rule(count: int, per: str | timedelta, action, scope: str = "ip")
Reject(status_code: int = 429, detail: str = "Rate limit exceeded")
Delay(seconds: float)
Throttle(bytes_per_sec: int)
```

### 期間とスコープ

- `per` は `second`、`minute`、`hour`、または正の `datetime.timedelta` を受け付けます。`timedelta` は1秒単位で指定してください。
- `scope` の既定値は `ip` です。`ip`、`default`、または登録済みの独自スコープ名を指定できます。前後の空白は検証時に除去されます。
- `Delay.seconds` は正の有限値に限られます。NaNや無限大は指定できません。待機中は0.1秒ごとに終了モードABORTを確認し、中断・キャンセル時には待機タスクもキャンセルします。

スコープごとの集計方法と使用例は[リクエスト数の制限とスコープ](policies.ja.md)を参照してください。

### アクションの選択

複数のルールに一致した場合は、次の順でアクションを1つ選びます。

1. `priority` が小さいもの。組み込みアクションでは `Reject` (0)、`Delay` (1)、`Throttle` (2) の順です。
2. `priority` が同じなら、`sort_key` が小さいもの。`Delay` は待機時間が長いもの、`Throttle` は転送速度が低いものが優先されます。
3. 両方とも同じなら、`limit_rules([...])` で先に定義されたルール。

## 独自のアクション

`ActionProtocol` を実装し、`decide()` から `PolicyDecision` を返します。必要なメンバーは次のとおりです。

- `priority: int`
- `sort_key: int | float`
- `to_dict() -> dict[str, Any]`
- `decide(retry_after: int) -> PolicyDecision`

組み込みアクションも `priority`、`sort_key`、`to_dict()` を持ちます。独自のアクションの優先順位も、上記の選択規則に従います。`Action` は `ActionProtocol` の型エイリアスです。

`PolicyDecision` は、ルールに一致したときの処理を表します。

| フィールド | 内容 |
| --- | --- |
| `reject` | 直ちにエラーレスポンスを返すかどうか |
| `reject_status` | 拒否時のHTTPステータスコード |
| `reject_detail` | JSONボディに返す詳細メッセージ |
| `retry_after` | `Retry-After` ヘッダーに設定する値 |
| `pre_delay` | エンドポイント実行前の待機時間 |
| `throttle_rate` | レスポンスに一時的に適用する転送速度の上限（バイト/秒） |

## ミドルウェアとユーティリティ

`ResponseBandwidthLimiterMiddleware` は帯域制限とリクエスト数制限を適用します。通常は `limiter.init_app(app)` で登録してください。

| 関数 | 戻り値 |
| --- | --- |
| `get_endpoint_name(request)` | リクエストに対応するエンドポイント名 |
| `get_route_path(request)` | リクエストに対応するルートパス |

設定に使う識別子を調べる場合は、limiterの `resolve_handler_identifier(request)` を使用してください。

---

[インストールと基本的な使い方](usage.ja.md) · [リクエスト数の制限とスコープ](policies.ja.md) · [Redis・運用設定・移行](operations.ja.md) · [開発と配布物の検証](development.ja.md)
