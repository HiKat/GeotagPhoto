---
name: release-build
description: GeotagPhoto のリリース候補作成、Windows exe/ZIP ビルド、ローカル releases/latest 更新、ユーザーレビュー、GitHub 公開、公開後検証を行う。ユーザーが「リリースしたい」「ビルドしたい」「新バージョンを公開したい」「latest を更新したい」と依頼した場合に使用する。
---

# リリースビルド手順

## 前提ルール
- 開発は常に **dev** ブランチで行う。**main** ブランチは公開リリース済みの状態のみ保持する。
- ビルド成果物・ZIP・一時ファイルはすべて `releases/v{X.Y.Z}/` に配置する（`dist_package/` は使用しない）。
- `releases/latest` は、**最新の機械検証済みローカル候補版**を指すディレクトリ・シンボリックリンクとする。GitHub で公開済みであることの証明には使わない。
- 候補版の整合検証が完了した直後、ユーザーレビュー前に `releases/latest` を更新する。コピーやジャンクションで代用しない。
- `releases/latest` の更新は同梱スクリプトだけで行う。既存リンクを先に削除する手順は禁止する。
- `releases/` ディレクトリは `.gitignore` に含まれており、リポジトリにはコミットしない。
- `SPEC.md` は Git で追跡し、リリース前に最新状態へ更新する。バージョンごとに機能を明示する。
- バージョンディレクトリや ZIP が既に存在する場合は上書きしない。内容を確認し、再作成にはユーザーの承認を得る。
- Python、git、gh、Nuitka の終了コードが非ゼロなら即時停止する。失敗した工程を成功扱いしない。

---

## ステップ1: リリース開始（バージョン番号の決定）

ユーザーが「リリースしたい」と言った時点で、以下を実施する。

1. **前回リリースからの変更内容を自動収集する**
   - `git log` で前回タグからの差分コミットを取得
   - 変更内容を「新機能」「改善」「バグ修正」に分類してユーザーに提示する
2. **バージョン番号の見解を示す**
   - **メジャー** (vX.0.0): 破壊的変更、大規模な機能追加
   - **マイナー** (v1.X.0): 後方互換のある新機能追加
   - **パッチ** (v1.0.X): バグ修正、軽微な改善
   - 変更内容に基づき、エージェントなりの推奨バージョンを提示する
3. **ユーザーにバージョン番号の確認を求める**
   - 確定したバージョンを `vX.Y.Z` 形式で記録する

4. **確定バージョンを一度だけ変数へ設定する**
   ```powershell
   Set-StrictMode -Version Latest
   $ErrorActionPreference = "Stop"

   $version = "X.Y.Z"
   $tag = "v$version"
   $fileVersion = "$version.0"
   $repository = "HiKat/GeotagPhoto"
   $repoRoot = (Resolve-Path -LiteralPath ".").Path
   $releaseRoot = Join-Path $repoRoot "releases"
   if (-not (Test-Path -LiteralPath $releaseRoot)) {
       New-Item -ItemType Directory -Path $releaseRoot | Out-Null
   }
   $releaseRootItem = Get-Item -LiteralPath $releaseRoot -Force
   if (-not $releaseRootItem.PSIsContainer -or
       ($releaseRootItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
       throw "releases はリポジトリ直下の実ディレクトリである必要があります: $releaseRoot"
   }
   $releaseDir = Join-Path $releaseRoot $tag
   $distDir = Join-Path $releaseDir "GeotagPhoto"
   $zipPath = Join-Path $releaseDir "GeotagPhoto-$tag-win64.zip"

   if ($tag -notmatch '^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') {
       throw "バージョン形式が不正です: $tag"
   }
   ```
   - 以後、`vX.Y.Z` の手置換やバージョンの再推測をしない。

---

## ステップ2: リリース前チェック（エージェント側で完結）

ユーザーに手動テストを依頼する前に、以下のチェックをエージェント側で実施する。

1. **ブランチ、作業ツリー、仮想環境を確認する**
   ```powershell
   $currentBranch = git branch --show-current
   if ($LASTEXITCODE -ne 0) { throw "現在ブランチを取得できません。" }
   if ($currentBranch -cne "dev") { throw "dev ブランチで実行してください。現在: $currentBranch" }

   $worktreeStatus = git status --porcelain
   if ($LASTEXITCODE -ne 0) { throw "作業ツリーの状態を取得できません。" }
   if ($worktreeStatus) { throw "未コミット変更があります。先に内容を確定してください。" }

   & "myenv\Scripts\python.exe" --version
   if ($LASTEXITCODE -ne 0) { throw "仮想環境 myenv が利用できません。リリースを停止します。" }
   ```
   - 仮想環境が壊れている場合、無断で Python のインストールや環境再作成を行わない。状態を報告し、復旧後に再開する。

2. **認証情報・設定ファイルが含まれていないことを確認する**

   同梱スキャナーで、現在のGit追跡ファイルと前回公開タグから現在の
   `dev` までの各コミット時点を検査する。

   ```powershell
   & "myenv\Scripts\python.exe" `
     ".github\skills\release-build\scripts\Test-CredentialExposure.py" `
     --repository-root . `
     --target-ref dev
   if ($LASTEXITCODE -ne 0) { throw "認証情報チェックに失敗しました。" }
   ```

   スキャナーは次を検査する。

   - `main.py` のハードコードされたメール、パスワード、APIキー／トークン
   - Git追跡対象の `.env`、`config.json`、`garmin_tokens`、`credentials`、`.token` 等
   - 前回公開タグから現在までの各コミットに、一時的に追加後削除された認証情報がないこと
   - 秘密鍵、AWS／GitHub／Google／Slack／Stripeの高確度トークン形式
   - 検出値そのものは標準出力へ表示せず、種類とファイル位置だけを報告する

   `credential-exposure-check: PASS` と `secret-values-printed: no` の両方が
   出力されることを確認する。候補を検出した場合は、値をログへ出さずに
   誤検知か実クレデンシャルかを確認し、解消するまでリリースを停止する。

3. **README.md と SPEC.md をビルド前に更新する**
   - `README.md` の「最新」ダウンロード表記、リリース URL、ZIP 名を `$tag` に更新する。過去版の例示は変更しない。
   - `SPEC.md` の現在仕様、変更内容、ビルド方針、リリース前チェック、リリース履歴を `$tag` で更新する。
   - `SPEC.md` が追跡対象であることを確認する。
   ```powershell
   git ls-files --error-unmatch SPEC.md
   if ($LASTEXITCODE -ne 0) { throw "SPEC.md が Git の追跡対象ではありません。" }

   Select-String -Path README.md -Pattern "最新|releases/tag/|GeotagPhoto-v[0-9]+\.[0-9]+\.[0-9]+-win64.zip"
   Select-String -Path SPEC.md -Pattern ([regex]::Escape("[$tag]"))
   ```
   - README/SPEC の変更を dev のリリース対象コミットへ含めてから、クリーンな作業ツリーで次へ進む。タグ作成後に README だけを追加コミットしない。

4. **構文チェック**
   ```powershell
   & "myenv\Scripts\python.exe" -c "import py_compile; py_compile.compile(r'main.py', doraise=True); print('OK')"
   if ($LASTEXITCODE -ne 0) { throw "main.py の構文チェックに失敗しました。" }
   ```
5. **テスト実行**
   - `test/`（ローカルテスト）と `tests/`（追跡対象の自動テスト）配下をすべて実行する
   ```powershell
   $testPaths = @("test", "tests") | Where-Object { Test-Path $_ }
   if (@($testPaths).Count -eq 0) { throw "テストディレクトリがありません。" }
   & "myenv\Scripts\python.exe" -m pytest $testPaths -v
   if ($LASTEXITCODE -ne 0) { throw "pytest に失敗しました。" }
   ```
   - pytest が未インストール、またはテストを収集できない場合も停止する。pytest 形式のファイルを直接実行して代用しない。
6. **エラーがあれば修正してからステップ3へ進む**

---

## ステップ3: ビルド

### 3.1 ディレクトリ作成
```powershell
if (Test-Path -LiteralPath $releaseDir) {
    throw "候補版ディレクトリが既に存在します。自動上書きしません: $releaseDir"
}
New-Item -ItemType Directory -Path $releaseDir | Out-Null
```

### 3.2 Nuitka ビルド実行

ステップ1で確定した変数を使用し、バージョン文字列を手置換しない。

```powershell
& "myenv\Scripts\python.exe" -m nuitka --standalone --enable-plugin=tk-inter `
  --msvc=latest `
  --include-package=customtkinter --include-package=tkcalendar `
  --include-package=tkintermapview --include-package=garminconnect `
  --include-package=babel `
  --include-data-dir=static=static `
  --include-data-dir=myenv/Lib/site-packages/customtkinter=customtkinter `
  --windows-console-mode=disable `
  --output-filename=GeotagPhoto.exe `
  --output-dir="$releaseDir" `
  --assume-yes-for-downloads `
  --windows-company-name="GeotagPhoto Project" `
  --windows-product-name="GeotagPhoto" `
  --windows-file-version="$fileVersion" `
  --windows-product-version="$fileVersion" `
  --windows-file-description="Photo geotagging tool using Garmin Connect GPX" `
  --windows-icon-from-ico=static/logo/app.ico `
  main.py
if ($LASTEXITCODE -ne 0) { throw "Nuitka ビルドに失敗しました。" }
```

> **`--msvc=latest` について（必須）**:
> Nuitka デフォルトの Zig バックエンドは、ビルドホスト（AMD Zen 4 等）の CPU 向けに AVX-512 命令を埋め込み、AVX-512 非対応の Intel コンシューマ CPU（Alder Lake 以降、Arrow Lake 含む）で `STATUS_ILLEGAL_INSTRUCTION (0xc000001d)` を起こし起動失敗する。
> MSVC バックエンドはベースライン x86-64 を出力するため、Intel/AMD 任意の x64 CPU で動作する。
> 事前に Visual Studio Build Tools 2022（C++ デスクトップ開発ワークロード）のインストールが必要。詳細は [docs/cross-cpu-compatibility.md](../../../docs/cross-cpu-compatibility.md) を参照。

> **`--output-dir` について**:
> ビルド中間物（`main.build/`）と成果物（`main.dist/`）の出力先を `releases/vX.Y.Z/` に指定する。
> 未指定の場合、プロジェクトルートに `main.build/` `main.dist/` が生成され散乱する。

> **`--windows-file-version` / `--windows-product-version` について**:
> これらは EXE ファイルの Windows プロパティ情報（右クリック→プロパティ→詳細タブ）に埋め込まれる。
> - ユーザーが使用中バージョンを確認できる
> - Windows Defender SmartScreen のレピュテーション管理に使われる
> - 障害報告時のバイナリ特定に必須
> - 形式は `X.Y.Z.0` の4桁固定（タグ `vX.Y.Z` に対応）

### 3.3 ビルド成果物のコピー

```powershell
# main.dist/ を候補版の GeotagPhoto/ にコピー
Copy-Item -Recurse (Join-Path $releaseDir "main.dist") $distDir

# ライセンスファイルをコピー
Copy-Item COPYING $distDir
Copy-Item NOTICE.md $distDir
Copy-Item THIRD_PARTY_NOTICES.md $distDir
Copy-Item README.md (Join-Path $distDir "README.txt")
if (-not (Test-Path -LiteralPath third_party_licenses -PathType Container)) {
    throw "third_party_licenses がありません。"
}
Copy-Item -Recurse third_party_licenses $distDir
```

> `README.txt` は root の `README.md` から必ず生成する。候補版フォルダー内だけを手編集しない。

### 3.4 ビルド中間物のクリーンアップ

```powershell
$buildDir = Join-Path $releaseDir "main.build"
$nuitkaDistDir = Join-Path $releaseDir "main.dist"
foreach ($path in @($buildDir, $nuitkaDistDir)) {
    $resolved = (Resolve-Path -LiteralPath $path -ErrorAction Stop).Path
    if (-not $resolved.StartsWith($releaseDir + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "クリーンアップ対象が候補版ディレクトリ外です: $resolved"
    }
}
Remove-Item -Recurse -Force -LiteralPath $buildDir, $nuitkaDistDir
```

> `--output-dir` により中間物は `releases/vX.Y.Z/` 内に生成されるため、コピー後に削除する。

### 3.5 ZIP 作成

```powershell
if (Test-Path -LiteralPath $zipPath) { throw "ZIP が既に存在します: $zipPath" }
Compress-Archive -Path $distDir -DestinationPath $zipPath
```

命名規則: `GeotagPhoto-$tag-win64.zip`（例: `GeotagPhoto-v1.2.4-win64.zip`）

### 3.6 リリースノート作成

`$releaseDir/release_notes.md` を以下のフォーマットで作成する。

```markdown
## 変更内容 (vA.B.C → vX.Y.Z)

### 新機能
- （該当する変更を記載）

### 改善
- （該当する変更を記載）

### バグ修正
- （該当する変更を記載）

### 利用方法
ダウンロード、インストール、利用方法、その他の情報についての詳細は[READMEページ](https://github.com/HiKat/GeotagPhoto/blob/main/README.md)を参照ください。
```

- セクションに該当する変更がない場合、そのセクションは省略する
- 前回リリースからの全変更をまとめて記載する

### 3.7 候補版の機械検証と `releases/latest` 更新

リリースノート作成後、同梱スクリプトを実行する。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File ".github\skills\release-build\scripts\Update-LatestRelease.ps1" `
  -Version $tag `
  -Phase Candidate
if ($LASTEXITCODE -ne 0) { throw "候補版の整合検証または latest 更新に失敗しました。" }

& "myenv\Scripts\python.exe" `
  ".github\skills\release-build\scripts\Test-CredentialExposure.py" `
  --repository-root . `
  --target-ref dev `
  --release-dir $releaseDir `
  --zip-path $zipPath
if ($LASTEXITCODE -ne 0) { throw "候補版の認証情報チェックに失敗しました。" }
```

このスクリプトは次をすべて確認してから `latest` を更新する。

- 配布フォルダー、ZIP、リリースノート、README、ライセンス、必須アイコンの存在
- EXE の FileVersion / ProductVersion が `$fileVersion` と一致
- 配布フォルダーと ZIP の全ファイルについて、相対パス・サイズ・SHA-256 が完全一致
- ZIP に絶対パス、親参照、ADS、重複、大小文字衝突、シンボリックリンクがない
- `SPEC.md`、同梱 `README.txt`、リリースノートのバージョン表記が `$tag` と一致
- `latest` がディレクトリ・シンボリックリンクで、候補版を指し、EXE ハッシュも一致
- 配布フォルダーとZIP内の危険な設定ファイル名、高確度トークン、秘密鍵が検出されない

検証開始前から `releases` 内の排他ロックを保持する。Candidate では旧版への逆行を拒否し、新しい一時リンクを先に作成・検証してから旧リンクを退避・切替する。権限不足や検証失敗時は旧リンクを保持または復元する。通常ファイル、通常フォルダー、ジャンクション、前回失敗時の退避リンクがある場合は自動変更しない。

> Windows PowerShell 5.1 の既定実行ポリシーでも、リポジトリ内の検証済みスクリプトを実行できるよう、この呼び出しに限り `-ExecutionPolicy Bypass` を指定する。ダウンロードした任意スクリプトには使用しない。

---

## ステップ4: ユーザーレビュー（一括）

ビルド・パッケージング完了後、以下の **4項目をまとめて** ユーザーに提示し、一度のレビューで承認を得る。

### 4.1 レビュー準備（エージェント側で実施）

1. **リリースノートの作成**: ステップ3.6 で `releases/vX.Y.Z/release_notes.md` を作成済みであること
2. **dev → main のマージ検証**: マージ前に以下を確認する
   ```powershell
   # 作業ツリーを変更せず、マージ結果の tree を計算する
   $mergeTree = git merge-tree --write-tree main dev
   if ($LASTEXITCODE -ne 0) { throw "dev から main へのマージ競合があります。" }

   $mergeDiff = git diff --stat main...dev
   if ($LASTEXITCODE -ne 0) { throw "main と dev の差分を取得できません。" }
   $mergeTree
   $mergeDiff
   ```
   - コンフリクトの有無、変更ファイル一覧を記録する
   - コンフリクトがある場合は解決方針をまとめる

### 4.2 ユーザーへのレビュー依頼

以下の4項目を **1つのメッセージで** ユーザーに提示する:

1. **EXE 動作確認**: `$distDir/GeotagPhoto.exe` の動作確認を依頼
2. **リリースノートレビュー**: `releases/vX.Y.Z/release_notes.md` の内容を提示
3. **マージ検証結果**: コンフリクトの有無、変更ファイル一覧を報告。問題がある場合は詳細を提示
4. **候補版の同一性**: `releases/latest` のリンク先、EXE バージョン、配布ファイル数、EXE/ZIP の SHA-256、`LatestUpdated` を提示

- レビュー対象は必ず `$tag` のバージョン付きパスと `releases/latest` の両方を明示する。
- 両経路の EXE ハッシュが一致しなければ、レビューを依頼せずステップ3.7へ戻る。
- この時点の `latest` は検証済みローカル候補版であり、GitHub 公開済みとは限らないことを明記する。

### 4.3 レビュー結果の反映

- ユーザーから全項目 OK が出たらステップ5へ進む
- 修正が必要な場合はステップ2に戻る

---

## ステップ5: 公開

ユーザーからレビュー OK が出たら、以下を一括で実施する。

### 公開中の失敗ルール

- リモートへの push 前に失敗した場合は、GitHub Release、公開完了報告へ進まない。
- main／タグの push または GitHub Release 作成後に失敗した場合は「部分公開」と報告し、成功済み工程と失敗工程を列挙する。
- 公開済みタグの削除・付け替え、GitHub Release の削除、バージョンディレクトリの削除を自動実行しない。同じ `$tag` で安全に再開する。
- `releases/latest` の更新・再検証に失敗しても、公開済み成果物を巻き戻さない。リンクの元状態と復旧用パスを報告し、公開完了とは扱わない。

### 5.1 main ブランチへのマージ
```powershell
git checkout main
if ($LASTEXITCODE -ne 0) { throw "main へ切り替えられません。" }
git merge dev
if ($LASTEXITCODE -ne 0) { throw "dev を main へマージできません。" }
```

### 5.2 タグの作成とプッシュ
```powershell
$mainCommit = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw "main のコミットを取得できません。" }

$tagCommit = git rev-parse --verify "refs/tags/$tag^{commit}" 2>$null
if ($LASTEXITCODE -eq 0) {
    if ($tagCommit.Trim() -cne $mainCommit) {
        throw "既存タグ $tag は現在の main と異なるコミットです。自動で付け替えません。"
    }
}
else {
    git tag $tag
    if ($LASTEXITCODE -ne 0) { throw "ローカルタグを作成できません。" }
}

# main とタグを一括 push し、片方だけ公開される状態を避ける
git push --atomic origin main $tag
if ($LASTEXITCODE -ne 0) { throw "main とタグの atomic push に失敗しました。リモート状態を確認してください。" }
```

### 5.3 GitHub リリースの作成
```powershell
# GitHub CLI の PATH 追加（必要に応じて）
$env:PATH += ";C:\Program Files\GitHub CLI"

# 既存 Release は上書きせず、後続の Published 検証へ送る。
$releaseLookup = gh api --include "repos/$repository/releases/tags/$tag" 2>&1
$releaseLookupExit = $LASTEXITCODE
$releaseLookupText = ($releaseLookup | Out-String)
if ($releaseLookupExit -eq 0) {
    Write-Host "GitHub Release は既に存在します。内容検証へ進みます: $tag"
}
elseif ($releaseLookupText -match 'HTTP/\S+\s+404') {
    gh release create $tag `
      $zipPath `
      --repo $repository `
      --title $tag `
      --notes-file (Join-Path $releaseDir "release_notes.md")
    if ($LASTEXITCODE -ne 0) { throw "GitHub Release の作成に失敗しました。部分公開状態を確認してください。" }
}
else {
    throw "GitHub Release の有無を確認できません。作成を試みず停止します: $releaseLookupText"
}
```

### 5.4 公開状態と `releases/latest` の最終検証

候補版で更新済みの `latest`、Git の公開状態、GitHub Release を同梱スクリプトで一括検証する。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File ".github\skills\release-build\scripts\Update-LatestRelease.ps1" `
  -Version $tag `
  -Phase Published `
  -Repository $repository
if ($LASTEXITCODE -ne 0) { throw "公開後の整合検証に失敗しました。" }
```

`Published` は `latest` を変更しない読み取り専用ゲートである。Candidate の全検証に加え、タグ内 README/SPEC、ローカル・リモートの main/タグ、GitHub Release の公開状態・本文、ダウンロードした ZIP の SHA-256 まで確認する。このコマンドが成功するまでリリース完了と報告しない。

### 5.5 ツイート案の作成

リリース完了後、宣伝用のツイート案を作成してユーザーに提示する。

**フォーマット:**
```
vX.Y.Z をリリース。
（変更内容の要約を1〜2文で簡潔に記載）
https://github.com/HiKat/GeotagPhoto/releases/tag/vX.Y.Z
```

- リリースノートの内容をベースに、ユーザー向けの簡潔な表現にまとめる
- 技術的な内部実装の詳細は省き、ユーザーにとっての価値・変更点を伝える
- URLは必ずリリースページへのリンクを含める

### 5.6 dev ブランチに戻る
```powershell
git checkout dev
if ($LASTEXITCODE -ne 0) { throw "dev へ戻せません。リリース自体の公開状態を維持したまま作業ツリーを確認してください。" }
```

---

## チェックリスト（リリース完了前の最終確認）

- [ ] SPEC.md が Git で追跡され、バージョン付きで最新状態
- [ ] 構文チェック・テストが全てパス
- [ ] Git追跡内容・リリース差分履歴・配布フォルダー・ZIPの認証情報チェックがパス
- [ ] Candidate 検証が成功し、EXE の FileVersion / ProductVersion と配布フォルダー・ZIP の全ファイル SHA-256 が一致
- [ ] README.txt とライセンスファイル（COPYING, NOTICE.md, THIRD_PARTY_NOTICES.md, third_party_licenses/）が ZIP に同梱
- [ ] リリースノートが `releases/vX.Y.Z/release_notes.md` に作成済み
- [ ] `releases/latest` が今回の検証済みローカル候補版を指し、候補版と同じ EXE ハッシュ
- [ ] ユーザーによる動作確認が完了
- [ ] main ブランチにマージ済み
- [ ] main、origin/main、タグが同じコミット
- [ ] GitHub リリースが draft/prerelease ではなく、期待する ZIP アセットとリリースノートを公開済み
- [ ] Published 検証が成功
- [ ] README.md のリリースリンクがタグ対象コミット内で最新バージョンに更新済み
- [ ] ツイート案を作成・提示済み
