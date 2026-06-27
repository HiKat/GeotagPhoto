# クロスCPU互換性: Nuitka ビルド時の AVX-512 問題と対策

## 概要

GeotagPhoto は Nuitka でスタンドアロン exe にビルドして配布している。
配布先 PC で **例外コード `0xc000001d` (STATUS_ILLEGAL_INSTRUCTION)** によって即時クラッシュする問題が v1.1.0 で発生した。原因はビルド時に使用される C コンパイラ（Zig）が、ビルドホストの CPU 向けに **AVX-512 命令を含むバイナリを生成** していたためであり、AVX-512 非対応のクライアント CPU で動作しなくなる。

このドキュメントは恒久対策と背景を記述する。

---

## 症状（v1.1.0 で観測）

- 配布先 PC で exe をダブルクリックしても GUI が起動しない
- コンソール付きビルドでも何も出力されず即終了（Python 初期化前にネイティブ命令で死亡）
- Windows イベントビューア:
  - 例外コード: `0xc000001d` (STATUS_ILLEGAL_INSTRUCTION)
  - 障害モジュール: `GeotagPhoto.exe` 自身
  - フォールトオフセット: `0x00000000017bb5e3`
- 開発機では正常動作

## 原因

| 区分 | CPU | AVX-512 |
|---|---|---|
| **開発機（ビルドホスト）** | AMD Ryzen 7 8700G (Zen 4) | 対応 |
| **配布先（クラッシュ）** | Intel Core Ultra 9 285H (Arrow Lake-H) | **非対応** |

- Intel は Alder Lake (12 世代) 以降のコンシューマ CPU から **AVX-512 を削除** している。Arrow Lake (Core Ultra 200 シリーズ) も非対応。
- 一方 AMD Zen 4 は AVX-512 をフル対応している。
- Nuitka 4.0.7 のデフォルトバックエンドである **Zig コンパイラは、ホスト CPU 向けに最適化** されたコードを生成する傾向があり、AMD Zen 4 上でビルドすると AVX-512 命令が埋め込まれる。
- 結果として AVX-512 非対応の Intel コンシューマ CPU で `STATUS_ILLEGAL_INSTRUCTION` が発生する。

> 「新しい CPU なのに動かない」というのが直感に反するが、Intel コンシューマラインは **意図的に AVX-512 を削除している** ことが鍵。AMD は搭載、Intel コンシューマは非搭載というベンダー非対称が原因。

## 恒久対策: MSVC バックエンドの使用

Nuitka に `--msvc=latest` を指定して **Visual Studio の MSVC コンパイラ** を使用する。MSVC はデフォルトで AVX/AVX-512 を有効化せず、ベースライン x86-64 のバイナリを生成するため、Intel/AMD 両方の任意の x64 CPU で動作する。

### 必要なインストール

1. **Visual Studio Build Tools 2022** をインストール
   - ダウンロード: <https://visualstudio.microsoft.com/ja/downloads/> → 「Tools for Visual Studio」 → 「Build Tools for Visual Studio 2022」
2. インストーラで **「C++ によるデスクトップ開発」ワークロード** を選択
3. インストール完了（要再起動の場合あり、約 3〜6GB）

### ビルドコマンドへの追加

Nuitka のコマンドに以下のオプションを追加する:

```powershell
--msvc=latest `
```

リリースビルド・デバッグビルド双方のスキル（[.github/skills/release-build/SKILL.md](.github/skills/release-build/SKILL.md) / [.github/skills/debug-build/SKILL.md](.github/skills/debug-build/SKILL.md)）に既に組み込み済み。**今後のすべてのビルドで `--msvc=latest` を必ず指定する。**

## やってはいけないこと

- ❌ Zig バックエンド（Nuitka デフォルト）のまま配布用ビルドを作る
- ❌ `--lto=yes` などホスト最適化が強化される可能性のあるオプションを安易に追加する
- ❌ AMD 開発機でビルドした exe を、互換性検証なしに Intel コンシューマ CPU で配布する

## 検証手順

新規ビルドが AVX-512 を含まないことを確認するには、配布先 PC（または AVX-512 非対応 CPU の仮想環境）で実際に起動テストする。CI 上で AVX-512 を無効化したサンドボックスで検証することも可能だが、現状は実機検証で運用する。

## 検証結果（2026/06/28）

- `--msvc=latest` を指定してビルドした配布EXEを実行マシンで起動確認
- 以前発生していた `0xc000001d`（STATUS_ILLEGAL_INSTRUCTION）は再現せず、起動問題は解消
- 本件は **解決済み** とし、今後も MSVC バックエンドを継続使用する

## 関連

- 初出: v1.1.0 配布障害（2026年5月）
- 修正: v1.2.0 にて `--msvc=latest` を採用し、実行マシンで解決確認済み
- 参考: Nuitka 公式ドキュメント `--msvc` オプション
