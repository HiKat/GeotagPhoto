---
name: release-build
description: ユーザーから「リリースしたい」「ビルドしたい」「新バージョンを公開したい」等のリリース作業を依頼された場合に読み込んでください。exe ビルド、ZIP 作成、GitHub リリース公開の全手順を含みます。
---

# リリースビルド手順

## 前提ルール
- 開発は常に **dev** ブランチで行う。**main** ブランチは公開リリース済みの状態のみ保持する。
- ビルド成果物・ZIP・一時ファイルはすべて `releases/v{X.Y.Z}/` に配置する（`dist_package/` は使用しない）。
- `releases/` ディレクトリは `.gitignore` に含まれており、リポジトリにはコミットしない。
- SPEC.md はリリース前に最新状態に更新されていること（前提条件）。バージョンごとに機能を明示すること。

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

---

## ステップ2: リリース前チェック（エージェント側で完結）

ユーザーに手動テストを依頼する前に、以下のチェックをエージェント側で実施する。

1. **認証情報・設定ファイルが含まれていないことを確認する**

   以下の Python スクリプトを一時ファイル `check_creds.py` として実行し、完了後に削除する。

   ```python
   import re, subprocess

   # --- 1. main.py のハードコード検出 ---
   with open('main.py', encoding='utf-8-sig') as f:
       lines = f.readlines()

   code_ok = True
   email_pat = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
   pw_pat = re.compile(r'password\s*=\s*["\'][^"\']{3,}')
   api_pat = re.compile(r'(?i)(api_key|apikey|client_secret|access_token|auth_token)\s*=\s*["\'][^"\']+')

   skip = ['email_entry', 'email_frame', 'password_entry', 'pw_frame', 'erraticradar', 'show=', 'Email:', 'Password:']

   for i, line in enumerate(lines, 1):
       if any(s in line for s in skip):
           continue
       stripped = line.strip()
       if stripped.startswith('#'):
           continue
       for pat, label in [(email_pat, 'EMAIL'), (pw_pat, 'PASSWORD'), (api_pat, 'API_KEY')]:
           m = pat.search(line)
           if m:
               print(f'[HARDCODED/{label}] main.py line {i}: {stripped}')
               code_ok = False

   if code_ok:
       print('[main.py] No hardcoded credentials (OK)')

   # --- 2. git 追跡ファイルに認証情報・設定ファイルが含まれていないかチェック ---
   # config.json / garmin_tokens / .token / .env / credentials 等が追跡されていると
   # push 時にクレデンシャルが漏洩するリスクがある
   DANGER_PATTERNS = ['config.json', 'garmin_tokens', '.token', 'credentials', '.env', 'auth_token']
   result = subprocess.run(['git', 'ls-files'], capture_output=True, text=True)
   tracked = result.stdout.splitlines()

   git_ok = True
   for f in tracked:
       fl = f.replace('\\', '/').lower()
       # test/ 配下が追跡されていないか確認
       if fl.startswith('test/'):
           print(f'[GIT/test/ TRACKED] {f}  ← .gitignore に追加して git rm --cached で追跡解除してください')
           git_ok = False
           continue
       for pat in DANGER_PATTERNS:
           if pat.lower() in fl:
               print(f'[GIT/CREDENTIAL FILE TRACKED] {f}  ← .gitignore に追加して git rm --cached で追跡解除してください')
               git_ok = False
               break

   if git_ok:
       print('[git ls-files] No dangerous files tracked (OK)')
   ```

   ```powershell
   & "myenv\Scripts\python.exe" check_creds.py
   Remove-Item check_creds.py
   ```

   - 両方の出力が `(OK)` であればチェック通過
   - `[HARDCODED/...]` が出た場合: `main.py` の該当箇所を修正してから先へ進む
   - `[GIT/...]` が出た場合: `.gitignore` に追加して `git rm --cached <ファイル>` で追跡解除してから先へ進む

2. **SPEC.md の確認**
   - バージョン付きで最新状態に更新されているか確認する
   - 未更新の場合はリリース前に更新を完了させる
3. **構文チェック**
   ```powershell
   & "myenv\Scripts\python.exe" -c "import py_compile; py_compile.compile(r'main.py', doraise=True); print('OK')"
   ```
4. **テスト実行**
   - `test/` 配下のテストファイルをすべて実行する
   ```powershell
   & "myenv\Scripts\python.exe" -m pytest test/ -v
   ```
   - pytest が未インストールの場合は個別に実行:
   ```powershell
   Get-ChildItem test\*.py | ForEach-Object { & "myenv\Scripts\python.exe" $_.FullName }
   ```
5. **エラーがあれば修正してからステップ3へ進む**

---

## ステップ3: ビルド

### 3.1 ディレクトリ作成
```powershell
New-Item -ItemType Directory -Path "releases\vX.Y.Z" -Force
```

### 3.2 Nuitka ビルド実行

以下のテンプレートを使用する。`X.Y.Z` 部分を確定バージョンに置換する。

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
  --output-dir="releases\vX.Y.Z" `
  --assume-yes-for-downloads `
  --windows-company-name="GeotagPhoto Project" `
  --windows-product-name="GeotagPhoto" `
  --windows-file-version="X.Y.Z.0" `
  --windows-product-version="X.Y.Z.0" `
  --windows-file-description="Photo geotagging tool using Garmin Connect GPX" `
  --windows-icon-from-ico=myenv/Lib/site-packages/customtkinter/assets/icons/CustomTkinter_icon_Windows.ico `
  main.py
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
# releases/vX.Y.Z/main.dist/ を releases/vX.Y.Z/GeotagPhoto/ にリネームコピー
Copy-Item -Recurse "releases\vX.Y.Z\main.dist" "releases\vX.Y.Z\GeotagPhoto"

# ライセンスファイルをコピー
Copy-Item COPYING "releases\vX.Y.Z\GeotagPhoto\"
Copy-Item NOTICE.md "releases\vX.Y.Z\GeotagPhoto\"
Copy-Item THIRD_PARTY_NOTICES.md "releases\vX.Y.Z\GeotagPhoto\"
if (Test-Path third_party_licenses) {
    Copy-Item -Recurse third_party_licenses "releases\vX.Y.Z\GeotagPhoto\"
}
```

### 3.4 ビルド中間物のクリーンアップ

```powershell
Remove-Item -Recurse -Force "releases\vX.Y.Z\main.build", "releases\vX.Y.Z\main.dist"
```

> `--output-dir` により中間物は `releases/vX.Y.Z/` 内に生成されるため、コピー後に削除する。

### 3.5 ZIP 作成

```powershell
Compress-Archive -Path "releases\vX.Y.Z\GeotagPhoto" -DestinationPath "releases\vX.Y.Z\GeotagPhoto-vX.Y.Z-win64.zip"
```

命名規則: `GeotagPhoto-vX.Y.Z-win64.zip`（固定）

### 3.6 リリースノート作成

`releases/vX.Y.Z/release_notes.md` を以下のフォーマットで作成する。

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

---

## ステップ4: ユーザーレビュー（一括）

ビルド・パッケージング完了後、以下の **3項目をまとめて** ユーザーに提示し、一度のレビューで承認を得る。

### 4.1 レビュー準備（エージェント側で実施）

1. **リリースノートの作成**: ステップ3.6 で `releases/vX.Y.Z/release_notes.md` を作成済みであること
2. **dev → main のマージ検証**: マージ前に以下を確認する
   ```powershell
   # マージのドライラン（実際にはマージしない）
   git checkout main
   git merge --no-commit --no-ff dev
   git diff --stat HEAD   # 変更ファイル一覧を確認
   git merge --abort       # ドライランを中止
   git checkout dev
   ```
   - コンフリクトの有無、変更ファイル一覧を記録する
   - コンフリクトがある場合は解決方針をまとめる

### 4.2 ユーザーへのレビュー依頼

以下の3項目を **1つのメッセージで** ユーザーに提示する:

1. **EXE 動作確認**: `releases/vX.Y.Z/GeotagPhoto/GeotagPhoto.exe` の動作確認を依頼
2. **リリースノートレビュー**: `releases/vX.Y.Z/release_notes.md` の内容を提示
3. **マージ検証結果**: コンフリクトの有無、変更ファイル一覧を報告。問題がある場合は詳細を提示

### 4.3 レビュー結果の反映

- ユーザーから全項目 OK が出たらステップ5へ進む
- 修正が必要な場合はステップ2に戻る

---

## ステップ5: 公開

ユーザーからレビュー OK が出たら、以下を一括で実施する。

### 5.1 main ブランチへのマージ
```powershell
git checkout main
git merge dev
git push origin main
```

### 5.2 タグの作成とプッシュ
```powershell
git tag vX.Y.Z
git push origin vX.Y.Z
```

### 5.3 GitHub リリースの作成
```powershell
# GitHub CLI の PATH 追加（必要に応じて）
$env:PATH += ";C:\Program Files\GitHub CLI"

# リリース作成（リリースノートを転記し、ZIP をアセットとして添付）
gh release create vX.Y.Z `
  "releases\vX.Y.Z\GeotagPhoto-vX.Y.Z-win64.zip" `
  --repo HiKat/GeotagPhoto `
  --title "vX.Y.Z" `
  --notes-file "releases\vX.Y.Z\release_notes.md"
```

### 5.4 README.md のリリースリンク更新（dev ブランチで実施）

#### 更新が必要な箇所（grep で一括確認する）

```powershell
# README.md 内の旧バージョン参照を検索
Select-String -Path README.md -Pattern "v[0-9]+\.[0-9]+\.[0-9]+" | Format-Table LineNumber, Line
```

以下の箇所をすべて確認・更新する:

| 箇所 | 内容 | 例 |
|---|---|---|
| ダウンロードリンクテキスト | `GeotagPhoto vA.B.C（最新）` | → `GeotagPhoto vX.Y.Z（最新）` |
| リリースページURL | `releases/tag/vA.B.C` | → `releases/tag/vX.Y.Z` |
| ZIP ファイル名 | `GeotagPhoto-vA.B.C-win64.zip` | → `GeotagPhoto-vX.Y.Z-win64.zip` |

> **注意**: `v1.0.0` のような過去バージョンを「例示」として記載している箇所（GPLv3「対応ソース」セクション等）は更新不要。「最新」「ダウンロード」文脈にある箇所のみ更新する。

#### コミット・プッシュ

```powershell
git add README.md
git commit -m "README.md: 最新リリースリンクを vX.Y.Z に更新"
git push origin dev

# main にマージ
git checkout main
git merge dev
git push origin main
git checkout dev
```

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
```

---

## チェックリスト（リリース完了前の最終確認）

- [ ] SPEC.md がバージョン付きで最新状態
- [ ] 構文チェック・テストが全てパス
- [ ] EXE の Windows プロパティバージョンが正しい
- [ ] ライセンスファイル（COPYING, NOTICE.md, THIRD_PARTY_NOTICES.md, third_party_licenses/）が ZIP に同梱
- [ ] リリースノートが `releases/vX.Y.Z/release_notes.md` に作成済み
- [ ] ユーザーによる動作確認が完了
- [ ] main ブランチにマージ済み
- [ ] タグが作成・プッシュ済み
- [ ] GitHub リリースページにアセットとリリースノートがアップロード済み
- [ ] README.md のリリースリンクが最新バージョンに更新済み（dev→main マージ済み）
- [ ] ツイート案を作成・提示済み
