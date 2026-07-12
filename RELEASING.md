# リリース手順

この文書は `response-bandwidth-limiter` のメンテナ向けリリース手順です。
公開リポジトリに置くことを前提としており、API キーやパスワードは記載しません。

パッケージの公開には GitHub Actions と PyPI の Trusted Publishing（OIDC）を使用します。
そのため、PyPI API token を GitHub Secrets に登録する必要はありません。

## 公開の流れ

| Git の ref | `pyproject.toml` のバージョン | 公開先 |
| --- | --- | --- |
| `release/v0.3.0rc1` ブランチ | `0.3.0rc1` | TestPyPI |
| `release/v0.3.0` ブランチ | `0.3.0` | TestPyPI |
| `v0.3.0` タグ | `0.3.0` | PyPI |

`.github/workflows/publish.yml` はブランチ名またはタグ名と、
`pyproject.toml` のバージョンが完全に一致することを検証します。
たとえば `0.3.0rc1` では `release/v0.3.0rc1` を使用し、
`release/v0.3.0-rc1` は使用しません。

TestPyPI と PyPI にアップロードされたファイルは、同じバージョンで置き換えられません。
一度公開に成功したブランチへ追加の commit を push すると、同じバージョンの再公開となり失敗します。
修正が必要な場合は、必ず新しいバージョンを使用してください。

## 初回のみ必要な設定

GitHub の `Settings` → `Environments` で、次の Environment を設定します。

- `testpypi`: deployment branch を `release/v*` に制限する。
- `pypi`: deployment tag を `v*` に制限し、Required reviewers を必ず設定する。

TestPyPI と PyPI の各プロジェクトの `Manage` → `Publishing` には、次の
Trusted Publisher を登録します。

| 項目 | TestPyPI | PyPI |
| --- | --- | --- |
| Owner | `kirisaki77` | `kirisaki77` |
| Repository | `response-bandwidth-limiter` | `response-bandwidth-limiter` |
| Workflow filename | `publish.yml` | `publish.yml` |
| Environment | `testpypi` | `pypi` |

プロジェクトがすでに存在する場合は、pending publisher を作成するのではなく、
既存プロジェクトの `Manage` → `Publishing` から登録します。
Owner、Repository、Workflow filename、Environment は大文字・小文字を含めて
ワークフローの値と一致させてください。

## リリース作業の前提

リリース作業には workflow と同じ Python 3.14 を使用します。バージョン確認コマンドで
`tomllib` を使うため、少なくとも Python 3.11 が必要です。パッケージ自体が対応する
Python バージョンとは別の、メンテナ作業用の要件です。

```console
python --version
```

公開物の install 確認には、開発環境とは別の使い捨て virtual environment を使用します。
以下の確認コマンドは PowerShell 用です。作成物は `.gitignore` の対象である
`downloads/` の下に置きます。

## 1. リリース候補を TestPyPI に公開する

以下では次のリリースを `0.3.0`、最初の候補を `0.3.0rc1` としています。
実際のバージョンに読み替えてください。

まず最新の `main` から RC ブランチを作成します。

```console
git switch main
git pull --ff-only
git status --short --branch
git switch -c release/v0.3.0rc1
```

`pyproject.toml` のバージョンを更新します。

```toml
[project]
version = "0.3.0rc1"
```

バージョンと変更内容を確認し、開発環境でテストを実行します。

```console
python -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])"
git diff --check
python -m pytest -q
```

問題がなければ commit して push します。

```console
git add pyproject.toml
git diff --cached --check
git status --short
git commit -m "chore: prepare 0.3.0rc1"
git push -u origin release/v0.3.0rc1
```

push により GitHub Actions の `Publish distributions` が起動します。
`Publish distribution to TestPyPI` job が成功するまで待ってから公開物を確認します。

pip は `--index-url` と `--extra-index-url` の間に優先順位を設けません。
そこで、対象 wheel だけを TestPyPI から依存関係なしで download し、そのローカル
wheel を install します。依存パッケージは install 時に PyPI から取得します。

```powershell
$version = "0.3.0rc1"
$testRoot = Join-Path "downloads" "release-test-$version-$([guid]::NewGuid())"
$venv = Join-Path $testRoot "venv"
$dist = Join-Path $testRoot "dist"
$testPython = Join-Path $venv "Scripts\python.exe"

python -m venv $venv
New-Item -ItemType Directory -Path $dist | Out-Null
& $testPython -m pip download --no-cache-dir --no-deps --only-binary=:all: --index-url https://test.pypi.org/simple/ --dest $dist "response-bandwidth-limiter==$version"
$wheels = @(Get-ChildItem -LiteralPath $dist -Filter "*.whl")
if ($wheels.Count -ne 1) { throw "Expected exactly one wheel, found $($wheels.Count)." }
$wheel = $wheels[0].FullName
& $testPython -m pip install --no-cache-dir --index-url https://pypi.org/simple/ $wheel
& $testPython -c "import importlib.metadata; print(importlib.metadata.version('response-bandwidth-limiter'))"
& $testPython -c "import response_bandwidth_limiter; print('import OK')"
```

期待するバージョン `0.3.0rc1` が表示され、基本動作に問題がないことを確認します。

### RC に修正が必要な場合

`0.3.0rc1` が TestPyPI に公開済みなら、そのブランチには追加で push しません。
公開済み RC から新しいブランチを作り、バージョンを `0.3.0rc2` に更新してから
修正を commit します。

```console
git switch release/v0.3.0rc1
git pull --ff-only
git switch -c release/v0.3.0rc2
# 修正し、pyproject.toml の version を 0.3.0rc2 に更新する
python -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])"
git diff --check
python -m pytest -q
git add pyproject.toml
git add -p
git diff --cached --check
git status --short
git commit -m "chore: prepare 0.3.0rc2"
git push -u origin release/v0.3.0rc2
```

`git status --short` で、必要な修正だけが staged になっていることを確認してから
commit してください。RC が承認できるまで、`rc3`、`rc4` のように新しい
バージョンで同じ手順を繰り返します。

## 2. 安定版を TestPyPI に公開する

承認した最新 RC から安定版ブランチを作ります。次の例では `rc1` を使用していますが、
実際には承認した `rcN` を指定してください。

```console
git switch release/v0.3.0rc1
git pull --ff-only
git switch -c release/v0.3.0
```

`pyproject.toml` のバージョンを `0.3.0` に更新し、確認して push します。

```console
python -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])"
git diff --check
python -m pytest -q
git add pyproject.toml
git diff --cached --check
git status --short
git commit -m "chore: prepare 0.3.0"
git push -u origin release/v0.3.0
```

GitHub Actions の TestPyPI 公開が成功した後、安定版も確認します。
「リリース候補を TestPyPI に公開する」の PowerShell ブロック全体をもう一度実行し、
先頭のバージョンだけを次のように変更します。GUID を含む新しい path が作られるため、
RC の確認環境は再利用されません。

```powershell
$version = "0.3.0"
```

安定版の確認後、GitHub で `release/v0.3.0` から `main` への Pull Request を作成します。
CI が成功していることを確認してからマージします。RC ブランチを直接 `main` に
マージする必要はありません。

安定版を TestPyPI に公開した後で修正が必要になった場合も、`0.3.0` を再利用しません。
たとえば `0.3.1rc1` のような新しいバージョンからやり直します。

## 3. PyPI に公開する

Pull Request のマージと `main` の CI 成功を確認してから、最新の `main` にタグを
付けます。タグを先に作成しないでください。

```console
git fetch origin
git switch main
git pull --ff-only
git status --short --branch
python -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])"
```

作業ツリーが空で、表示されたバージョンが `0.3.0` であることを確認します。
その後、annotated tag を作成して内容を確認し、push します。

```console
git tag -a v0.3.0 -m "v0.3.0"
git show --no-patch --decorate v0.3.0
git push origin v0.3.0
```

タグの push により PyPI 公開用の workflow が起動します。GitHub Environment
`pypi` の Required reviewer は、version、commit、実行中の job を確認して承認します。
`Publish distribution to PyPI` job が成功した後、使い捨ての virtual environment
で PyPI の公開物を確認します。

```powershell
$version = "0.3.0"
$testRoot = Join-Path "downloads" "pypi-test-$version-$([guid]::NewGuid())"
$venv = Join-Path $testRoot "venv"
$testPython = Join-Path $venv "Scripts\python.exe"

python -m venv $venv
& $testPython -m pip install --no-cache-dir --index-url https://pypi.org/simple/ "response-bandwidth-limiter==$version"
& $testPython -c "import importlib.metadata; print(importlib.metadata.version('response-bandwidth-limiter'))"
& $testPython -c "import response_bandwidth_limiter; print('import OK')"
```

## トラブル対応

### タグを main へのマージ前や誤った commit に push した

まず GitHub Actions で該当 workflow をキャンセルし、対象バージョンが PyPI に
公開されていないことを PyPI のプロジェクトページで確認します。未公開であることを
確認できた場合に限り、誤ったタグを削除します。

タグの削除も GitHub の `push` event になります。このリポジトリの workflow は
`github.event.deleted == false` を要求し、PyPI 公開ではさらに
`github.event.created == true` を要求することで、削除や force-update による
再公開を防止しています。この条件を削除しないでください。

```console
git push origin --delete v0.3.0
git tag -d v0.3.0
```

正しい commit が `main` にマージされた後、通常の手順でタグを作り直します。

すでに PyPI への公開が成功している場合、同じバージョンは再利用できません。
必要に応じて PyPI 上で該当 release を yank し、修正版を新しいバージョンとして
リリースします。タグを削除しても PyPI の公開物は削除されません。

### `File already exists` または同一バージョンのエラーになる

TestPyPI と PyPI は同一バージョン・同一ファイル名の再アップロードを許可しません。
`skip-existing` で回避せず、RC なら `rc2`、安定版なら次の修正版のように
新しいバージョンを使用します。

### `No matching distribution found` になる

次の順序で確認します。

1. GitHub Actions の公開 job が成功しているか確認する。
2. TestPyPI または PyPI のプロジェクトページに対象バージョンが表示されるまで待つ。
3. install コマンドが公開先と完全なバージョン（`0.3.0rc1` など）を指定しているか確認する。
4. pip の古いキャッシュを使わないよう `--no-cache-dir` を指定する。

`Cache entry deserialization failed` という warning 自体は、通常は公開失敗を意味しません。
その後に表示される対象バージョン一覧と GitHub Actions の結果を確認してください。

### Trusted Publisher の認証に失敗する

TestPyPI または PyPI の `Manage` → `Publishing` で、Owner、Repository、Workflow
filename、Environment が「初回のみ必要な設定」の値と完全に一致しているか確認します。
GitHub Environment 名は `testpypi` と `pypi` であり、Secret の名前ではありません。

## 公開リポジトリに記載しない情報

この手順、ブランチ名、Environment 名、workflow filename、PyPI のプロジェクト名は
公開されても問題ありません。一方、次の情報は commit、Issue、Pull Request、Actions
log に記載しないでください。

- PyPI/TestPyPI の API token、パスワード、2FA コード
- GitHub token、OIDC token、認証 cookie
- token を含む `.pypirc` やローカル設定ファイル
- 認証情報を含むコマンド出力やスクリーンショット

誤って認証情報を公開した場合は、履歴から消すだけでなく、先に該当 token を失効させて
ください。このリポジトリの通常の公開手順では API token は使用しません。

## 最終チェックリスト

- [ ] `pyproject.toml` のバージョンとブランチ名が一致している。
- [ ] TestPyPI の workflow が成功し、公開物を新しい環境へ install できた。
- [ ] 安定版ブランチの Pull Request と `main` の CI が成功した。
- [ ] タグはマージ後の `main` に付けた。
- [ ] PyPI Environment の承認内容（version、commit、artifact）を確認した。
- [ ] PyPI の workflow が成功し、公開物を新しい環境へ install できた。

## 参考資料

- [PyPI: Adding a Trusted Publisher to an Existing PyPI Project](https://docs.pypi.org/trusted-publishers/adding-a-publisher/)
- [PyPI: Trusted Publishers security model](https://docs.pypi.org/trusted-publishers/security-model/)
- [GitHub: Deployments and environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
- [GitHub: Push webhook payload](https://docs.github.com/en/webhooks/webhook-events-and-payloads#push)
- [pip install: `--extra-index-url` warning](https://pip.pypa.io/en/stable/cli/pip_install/#cmdoption-extra-index-url)
