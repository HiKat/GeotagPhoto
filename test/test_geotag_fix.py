"""
修正後の動作確認テスト
- _tz_name_to_offset のテスト
- run_exiftool_geotag の引数変更確認
- 実際のジオタグ付与テスト（Leica写真 + 複数GPX）
"""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import _tz_name_to_offset, run_exiftool_geotag, find_exiftool

def test_tz_name_to_offset():
    print("■ _tz_name_to_offset テスト")
    
    assert _tz_name_to_offset("Asia/Tokyo") == "+09:00", f"Asia/Tokyo: {_tz_name_to_offset('Asia/Tokyo')}"
    print(f"  Asia/Tokyo → {_tz_name_to_offset('Asia/Tokyo')} ✓")
    
    assert _tz_name_to_offset("UTC") == "+00:00", f"UTC: {_tz_name_to_offset('UTC')}"
    print(f"  UTC → {_tz_name_to_offset('UTC')} ✓")
    
    # 無効なTZ名はエラーにならず空文字を返す
    assert _tz_name_to_offset("Invalid/TZ") == "", f"Invalid: {_tz_name_to_offset('Invalid/TZ')}"
    print(f"  Invalid/TZ → (空文字) ✓")
    
    print("  → OK\n")


def test_geotag_with_multiple_gpx():
    print("■ 複数GPX + タイムゾーン指定での実際のジオタグ付与テスト")
    
    photo_src = Path(r"C:\Users\hisa4\Desktop\GeotagPhoto_取り込み先\without_geotag\2026-04-12")
    gpx_dir = Path(r"C:\Users\Public\Pictures\my_gpx")
    
    if not photo_src.exists():
        print("  スキップ: 写真ディレクトリが見つかりません")
        return
    
    # テスト用一時ディレクトリにLeica写真を数枚コピー
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # 11:37 JST (GPXカバー範囲内) と 16:19 JST (範囲外) の写真をコピー
        test_files = ["L1020243.JPG", "L1020260.JPG", "L1020281.JPG"]
        copied = 0
        for fname in test_files:
            src = photo_src / fname
            if src.exists():
                shutil.copy2(src, tmpdir / fname)
                copied += 1
        
        if copied == 0:
            print("  スキップ: テスト対象ファイルが見つかりません")
            return
        
        print(f"  {copied}枚の写真をテストディレクトリにコピー")
        
        # 04-12関連のGPXファイルを全て使用
        gpx_files = sorted(gpx_dir.glob("*2026-04-12*.gpx"))
        print(f"  GPXファイル数: {len(gpx_files)}")
        for gpx in gpx_files:
            print(f"    - {gpx.name}")
        
        exiftool_path = find_exiftool()
        file_extensions = {".jpg"}
        camera_tz_offset = _tz_name_to_offset("Asia/Tokyo")
        print(f"  カメラTZオフセット: {camera_tz_offset}")
        
        # run_exiftool_geotag を呼び出し（複数GPX + タイムゾーン指定）
        tagged, skipped = run_exiftool_geotag(
            exiftool_path, gpx_files, tmpdir, file_extensions,
            overwrite_existing=False, max_workers=1,
            camera_tz_offset=camera_tz_offset
        )
        print(f"  結果: {tagged}個に付与, {skipped}個スキップ")
        
        # 各ファイルのGPS情報を確認
        result = subprocess.run(
            [exiftool_path, '-json', '-FileName', '-GPSLatitude', '-GPSLongitude', str(tmpdir)],
            capture_output=True, text=True, encoding='utf-8', errors='replace'
        )
        data = json.loads(result.stdout)
        
        gps_count = 0
        for item in data:
            fname = item.get("FileName", "")
            lat = item.get("GPSLatitude")
            lon = item.get("GPSLongitude")
            has_gps = lat is not None and lon is not None
            if has_gps:
                gps_count += 1
            status = "✓ GPS付与" if has_gps else "✗ GPS無し"
            print(f"    {fname}: {status}")
        
        # L1020243 (11:37 JST, GPX範囲内) はGPSが付与されるはず
        # L1020260 (12:09 JST, GPX範囲内) はGPSが付与されるはず
        # L1020281 (16:19 JST, GPX範囲外) はGPSなしのはず
        expected_gps = 2
        assert gps_count == expected_gps, f"GPS付与数が期待値と異なる: {gps_count} (期待: {expected_gps})"
        print(f"  → OK (GPX範囲内{gps_count}枚に付与、範囲外はスキップ)\n")


if __name__ == '__main__':
    test_tz_name_to_offset()
    test_geotag_with_multiple_gpx()
    print("全テスト完了!")
