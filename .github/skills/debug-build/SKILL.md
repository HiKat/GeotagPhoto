---
name: debug-build
description: ユーザーから「デバッグ用ビルドを作りたい」「テスト用exeを作りたい」「デバッグ用の実行ファイルを配布したい」等のデバッグビルド作成を依頼された場合に読み込んでください。リリースビルドではなく、動作検証目的のexeビルド・ZIP作成手順を含みます。
---

# デバッグビルド手順

## 概要
リリース前の動作確認やテスト配布用に、デバッグ用のスタンドアロンexeをビルドしてZIPで提供する手順です。
リリースビルド（`release-build` スキル）とは異なり、バージョン番号の決定・SPEC.md更新・GitHub公開は行いません。

## 前提ルール
- デバッグビルドは `releases/v_debug-{説明ラベル}/` に配置する。
  - 例: `releases/v_debug-exiftool-fix/`, `releases/v_debug-garmin-token-auth/`
- 「説明ラベル」はブランチ名やチケット名など、何のデバッグビルドか分かる名前にする。
- `releases/` ディレクトリは `.gitignore` に含まれており、リポジトリにはコミットしない。
- ビルドは必ず仮想環境（`myenv`）内の Python を使用する。
- ビルド前に **構文チェック** と **テスト** を通しておくこと。

---

## ステップ1: ビルド前チェック

### 1.1 構文チェック
```powershell
& "myenv\Scripts\python.exe" -c "import py_compile; py_compile.compile(r'main.py', doraise=True); print('OK')"
```

### 1.2 テスト実行
```powershell
& "myenv\Scripts\python.exe" -m pytest test/ -v
```
- pytest が未インストールの場合:
```powershell
Get-ChildItem test\*.py | ForEach-Object { & "myenv\Scripts\python.exe" $_.FullName }
```

エラーがある場合は修正してからステップ2に進む。

---

## ステップ2: ディレクトリ作成

`{LABEL}` を説明ラベルに置き換える。

```powershell
New-Item -ItemType Directory -Path "releases\v_debug-{LABEL}" -Force
```

---

## ステップ3: Nuitka ビルド実行

デバッグビルドではバージョン番号を `0.0.0.0` 、file-description に `(debug build)` を付与する。

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
  --output-dir="releases\v_debug-{LABEL}" `
  --assume-yes-for-downloads `
  --windows-company-name="GeotagPhoto Project" `
  --windows-product-name="GeotagPhoto" `
  --windows-file-version="0.0.0.0" `
  --windows-product-version="0.0.0.0" `
  --windows-file-description="Photo geotagging tool using Garmin Connect GPX (debug build)" `
  --windows-icon-from-ico=myenv/Lib/site-packages/customtkinter/assets/icons/CustomTkinter_icon_Windows.ico `
  main.py
```

> **`--msvc=latest` について（必須）**:
> デバッグビルドであっても、配布検証用に他の PC で起動確認することを想定し MSVC バックエンドを使用する。Zig バックエンドはホスト CPU 向け AVX-512 命令を含めることがあり、Intel コンシューマ CPU（Alder Lake 以降）で `STATUS_ILLEGAL_INSTRUCTION (0xc000001d)` により起動失敗する。
> 事前に Visual Studio Build Tools 2022（C++ デスクトップ開発ワークロード）のインストールが必要。詳細は [docs/cross-cpu-compatibility.md](../../../docs/cross-cpu-compatibility.md) を参照。

> **リリースビルドとの違い:**
> - `--windows-file-version` / `--windows-product-version` は `0.0.0.0` 固定
> - `--windows-file-description` に `(debug build)` を付与
> - `--output-dir` は `releases\v_debug-{LABEL}` にする

---

## ステップ4: 成果物のパッケージング

### 4.1 GeotagPhotoフォルダにコピー
```powershell
Copy-Item -Recurse "releases\v_debug-{LABEL}\main.dist" "releases\v_debug-{LABEL}\GeotagPhoto"
```

### 4.2 ライセンスファイルをコピー
```powershell
Copy-Item COPYING "releases\v_debug-{LABEL}\GeotagPhoto\"
Copy-Item NOTICE.md "releases\v_debug-{LABEL}\GeotagPhoto\"
Copy-Item THIRD_PARTY_NOTICES.md "releases\v_debug-{LABEL}\GeotagPhoto\"
if (Test-Path third_party_licenses) {
    Copy-Item -Recurse third_party_licenses "releases\v_debug-{LABEL}\GeotagPhoto\"
}
```

### 4.3 ビルド中間物のクリーンアップ
```powershell
Remove-Item -Recurse -Force "releases\v_debug-{LABEL}\main.build", "releases\v_debug-{LABEL}\main.dist"
```

### 4.4 ZIP作成
```powershell
Compress-Archive -Path "releases\v_debug-{LABEL}\GeotagPhoto" -DestinationPath "releases\v_debug-{LABEL}\GeotagPhoto-debug-{LABEL}-win64.zip"
```

命名規則: `GeotagPhoto-debug-{LABEL}-win64.zip`

---

## ステップ5: 完了確認

```powershell
Get-ChildItem "releases\v_debug-{LABEL}\*.zip" | Select-Object Name, @{N='SizeMB';E={[math]::Round($_.Length/1MB,1)}}
```

ZIPファイルが存在し、サイズが適切（通常 50MB 以上）であることを確認する。

---

## 最終的なディレクトリ構成

```
releases/
  v_debug-{LABEL}/
    GeotagPhoto/               ← 展開済みのexeフォルダ（直接実行も可能）
      GeotagPhoto.exe
      COPYING
      NOTICE.md
      THIRD_PARTY_NOTICES.md
      third_party_licenses/
      ... (その他DLL等)
    GeotagPhoto-debug-{LABEL}-win64.zip  ← 配布用ZIP
```

---

## 注意事項
- デバッグビルドはあくまで動作確認用であり、正式リリースには `release-build` スキルを使用すること。
- 複数のデバッグビルドを同時に管理する場合は、ラベルで区別できるようにする。
- テスト完了後、不要になったデバッグビルドは `releases/v_debug-{LABEL}/` ごと削除してよい。
