"""
ジオタグ付与漏れの検証スクリプト
写真のタイムスタンプとGPXトラックの時間範囲を比較し、
マッチすべきだったか否かを診断する。
"""
import subprocess
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))

# パス設定
PHOTO_DIR = Path(r"C:\Users\hisa4\Desktop\GeotagPhoto_取り込み先\without_geotag\2026-04-12")
GPX_DIR = Path(r"C:\Users\Public\Pictures\my_gpx")

def parse_gpx_times(gpx_file: Path):
    """GPXファイルからすべてのトラックポイントの時刻を取得"""
    ns = {
        'gpx': 'http://www.topografix.com/GPX/1/1',
        'gpx10': 'http://www.topografix.com/GPX/1/0',
    }
    tree = ET.parse(gpx_file)
    root = tree.getroot()
    
    times = []
    # GPX 1.1
    for trkpt in root.findall('.//gpx:trkpt/gpx:time', ns):
        t = trkpt.text.strip()
        try:
            dt = datetime.fromisoformat(t.replace('Z', '+00:00'))
            times.append(dt)
        except:
            pass
    # GPX 1.0
    if not times:
        for trkpt in root.findall('.//gpx10:trkpt/gpx10:time', ns):
            t = trkpt.text.strip()
            try:
                dt = datetime.fromisoformat(t.replace('Z', '+00:00'))
                times.append(dt)
            except:
                pass
    # namespace-agnostic fallback
    if not times:
        for elem in root.iter():
            if elem.tag.endswith('}time') or elem.tag == 'time':
                if elem.text:
                    t = elem.text.strip()
                    try:
                        dt = datetime.fromisoformat(t.replace('Z', '+00:00'))
                        times.append(dt)
                    except:
                        pass
    return sorted(times)


def get_photo_times(photo_dir: Path):
    """ExifToolで写真の撮影日時を取得"""
    files = sorted([f for f in photo_dir.iterdir() if f.suffix.upper() == '.JPG'])
    if not files:
        files = sorted([f for f in photo_dir.iterdir() if f.is_file()])[:20]
    
    result = subprocess.run(
        ['exiftool', '-json', '-DateTimeOriginal', '-FileName'] + [str(f) for f in files],
        capture_output=True, text=True, encoding='utf-8', errors='replace'
    )
    data = json.loads(result.stdout)
    photo_times = []
    for item in data:
        fname = item.get('FileName', '')
        dto = item.get('DateTimeOriginal', '')
        if dto:
            try:
                dt = datetime.strptime(dto, "%Y:%m:%d %H:%M:%S")
                # カメラはJSTで記録されている前提
                dt_jst = dt.replace(tzinfo=JST)
                dt_utc = dt_jst.astimezone(timezone.utc)
                photo_times.append((fname, dt_jst, dt_utc))
            except:
                photo_times.append((fname, None, None))
    return sorted(photo_times, key=lambda x: x[1] if x[1] else datetime.min.replace(tzinfo=JST))


def main():
    print("=" * 80)
    print("ジオタグ付与漏れ検証レポート")
    print("=" * 80)
    
    # 1. GPXファイルの時間範囲を表示
    print("\n■ GPXファイルの時間範囲 (04-12関連)")
    gpx_files = sorted(GPX_DIR.glob("*2026-04-12*.gpx"))
    gpx_ranges = []
    all_gpx_times = []
    
    for gpx in gpx_files:
        times = parse_gpx_times(gpx)
        if times:
            start_utc = times[0]
            end_utc = times[-1]
            start_jst = start_utc.astimezone(JST)
            end_jst = end_utc.astimezone(JST)
            gpx_ranges.append((gpx.name, start_jst, end_jst, times))
            all_gpx_times.extend(times)
            print(f"  {gpx.name}")
            print(f"    UTC : {start_utc.strftime('%H:%M:%S')} ～ {end_utc.strftime('%H:%M:%S')}")
            print(f"    JST : {start_jst.strftime('%H:%M:%S')} ～ {end_jst.strftime('%H:%M:%S')}")
            print(f"    ポイント数: {len(times)}")
        else:
            print(f"  {gpx.name}: トラックポイントなし")
    
    if all_gpx_times:
        combined_start = min(all_gpx_times).astimezone(JST)
        combined_end = max(all_gpx_times).astimezone(JST)
        print(f"\n  → 04-12 GPX全体のカバー範囲(JST): {combined_start.strftime('%H:%M:%S')} ～ {combined_end.strftime('%H:%M:%S')}")
    
    # 2. 写真の撮影日時を取得
    print(f"\n■ 写真の撮影日時 ({PHOTO_DIR.name})")
    photos = get_photo_times(PHOTO_DIR)
    
    if not photos:
        print("  写真が見つかりませんでした")
        return
    
    print(f"  ファイル数: {len(photos)}")
    if photos:
        print(f"  最初: {photos[0][0]} → {photos[0][1].strftime('%H:%M:%S')} JST")
        print(f"  最後: {photos[-1][0]} → {photos[-1][1].strftime('%H:%M:%S')} JST")
    
    # 3. マッチング分析
    print("\n■ マッチング分析")
    print("  (ExifToolの -geotag は通常、最も近いトラックポイントとの時間差が")
    print("   GeoMaxIntSecs(デフォルト1800秒=30分)以内の場合に補間してタグ付けします)")
    
    matched = []
    unmatched = []
    GEOMAXINTSECS = 1800  # ExifTool default
    
    for fname, dt_jst, dt_utc in photos:
        if dt_utc is None:
            unmatched.append((fname, dt_jst, "日時なし", None))
            continue
        
        # 全GPXトラックポイントから最も近い時刻を探す
        min_diff = None
        closest_time = None
        closest_gpx = None
        
        for gpx_name, start_jst, end_jst, times in gpx_ranges:
            for t in times:
                diff = abs((dt_utc - t).total_seconds())
                if min_diff is None or diff < min_diff:
                    min_diff = diff
                    closest_time = t.astimezone(JST)
                    closest_gpx = gpx_name
        
        if min_diff is not None and min_diff <= GEOMAXINTSECS:
            matched.append((fname, dt_jst, closest_gpx, min_diff))
        else:
            unmatched.append((fname, dt_jst, closest_gpx, min_diff))
    
    print(f"\n  ✓ マッチ可能（GPXカバー範囲内）: {len(matched)} 件")
    print(f"  ✗ マッチ不可（GPXカバー範囲外）: {len(unmatched)} 件")
    
    if matched:
        print("\n  ── マッチ可能な写真 ──")
        for fname, dt_jst, gpx_name, diff in matched:
            print(f"    {fname}  {dt_jst.strftime('%H:%M:%S')} JST  "
                  f"← {gpx_name} (差: {int(diff)}秒)")
    
    if unmatched:
        print("\n  ── マッチ不可能な写真 ──")
        for fname, dt_jst, gpx_name, diff in unmatched:
            if diff is not None:
                print(f"    {fname}  {dt_jst.strftime('%H:%M:%S') if dt_jst else '???'} JST  "
                      f"← 最寄GPX: {gpx_name} (差: {int(diff)}秒 = {int(diff/60)}分)")
            else:
                print(f"    {fname}  日時情報なし")
    
    # 4. 根本原因の特定
    print("\n" + "=" * 80)
    print("■ 根本原因")
    print("=" * 80)
    print()
    print("  コードの処理フロー:")
    print("  1. gpx_files = list_gpx_or_tcx_files(gpx_dir)  → 更新日時の新しい順にソート")
    print("  2. gpx_file = gpx_files[0]  ← 最新の1ファイルのみ使用")
    print("  3. ExifToolで -geotag <1つのGPXファイル> を実行")
    print()
    print("  使用されたGPX: activity_2026-04-14_1_南区 バイク_22518144026.gpx")
    print("    → このGPXは 04-14 の12:23～13:03 JST のみカバー")
    print("    → 04-12 の写真はこのGPXの時間範囲外のため、全てタグ付け失敗")
    print()
    if unmatched:
        print(f"  さらに、全GPXファイルを使用しても {len(unmatched)} 件は")
        print("  GPXトラックの時間範囲外（30分以上離れている）のためタグ付け不可")


if __name__ == '__main__':
    main()
