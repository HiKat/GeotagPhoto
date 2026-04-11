# Copyright (C) 2026 @erraticradar_01
#
# This file is part of GeotagPhoto.
#
# GeotagPhoto is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# GeotagPhoto is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with GeotagPhoto. If not, see <https://www.gnu.org/licenses/>.

import base64
import io
import json
import logging
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Callable, List, Optional, Set, Tuple

import customtkinter as ctk
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)
from PIL import Image, ImageTk
from tkcalendar import Calendar
import tkinter as tk
from tkinter import filedialog, messagebox
import tkintermapview


# -----------------------------
# ここからは共通ユーティリティ
# -----------------------------


def encode_password(raw_password: str) -> str:
    """
    パスワードを簡易的にエンコードするための関数です。
    ※本格的な暗号化ではありませんが、
    設定ファイルにそのまま平文で保存しないための最低限の対策です。
    """
    if not raw_password:
        return ""
    # base64でバイト列を文字列に変換して返します。
    return base64.b64encode(raw_password.encode("utf-8")).decode("utf-8")


def decode_password(encoded_password: str) -> str:
    """
    encode_passwordで保存した文字列を元に戻す関数です。
    """
    if not encoded_password:
        return ""
    # base64の文字列を元の文字列へ復元します。
    return base64.b64decode(encoded_password.encode("utf-8")).decode("utf-8")


def find_exiftool() -> str:
    """
    ExifToolの実行ファイルを探します。

    1. スクリプトと同じフォルダに exiftool.exe があるか
    2. 環境変数PATHにあるか

    どちらかで見つかったパスを返します。
    """
    # このPythonファイルがあるフォルダを取得します。
    current_dir = Path(__file__).resolve().parent

    # 同じフォルダにexiftool.exeがあるかを確認します。
    local_exe = current_dir / "exiftool.exe"
    if local_exe.exists():
        return str(local_exe)

    # PATHにある場合は、"exiftool"で実行可能なためそのまま返します。
    return "exiftool"


def list_gpx_or_tcx_files(gpx_dir: Path) -> List[Path]:
    """
    GPXまたはTCXファイルを指定フォルダから取得します。
    """
    if not gpx_dir.exists():
        return []

    # 再帰的に.gpx/.tcxを探します。
    candidates: List[Path] = []
    for ext in (".gpx", ".tcx"):
        candidates.extend(gpx_dir.rglob(f"*{ext}"))

    # 更新日時の新しい順に並べます。
    return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)


def parse_gpx_track(gpx_file: Path) -> List[Tuple[float, float]]:
    """
    GPXまたはTCXファイルから軌跡の座標リストを取得します。
    
    Garmin Connect APIはデフォルトでTCX形式をダウンロードするため、
    .gpx拡張子でもTCX形式の場合があります。両形式に対応しています。
    
    Args:
        gpx_file: GPXまたはTCXファイルのパス
        
    Returns:
        List[Tuple[float, float]]: (緯度, 経度) のタプルのリスト
    """
    coordinates = []
    
    try:
        tree = ET.parse(gpx_file)
        root = tree.getroot()
        
        # ルート要素のタグから実際のフォーマットを判定します
        root_tag = root.tag.lower()
        
        # TCX形式の判定: ルートタグに "trainingcenterdatabase" が含まれる場合
        if 'trainingcenterdatabase' in root_tag:
            coordinates = _parse_tcx_track_coordinates(root)
        else:
            # GPX形式としてパース
            coordinates = _parse_gpx_track_coordinates(root)
        
    except Exception as e:
        print(f"GPXパースエラー ({gpx_file.name}): {e}")
    
    return coordinates


def _parse_gpx_track_coordinates(root: ET.Element) -> List[Tuple[float, float]]:
    """
    GPX形式のXMLルート要素から座標リストを取得します。
    GPXでは <trkpt lat="..." lon="..."> の形式で座標が格納されています。
    """
    coordinates = []
    
    # GPX名前空間の処理
    namespace = {'gpx': 'http://www.topografix.com/GPX/1/1'}
    
    # GPX 1.0の場合
    if 'http://www.topografix.com/GPX/1/0' in root.tag:
        namespace = {'gpx': 'http://www.topografix.com/GPX/1/0'}
    
    # 名前空間なしの場合も対応
    if not root.tag.startswith('{'):
        namespace = {}
    
    # トラックポイントを取得
    if namespace:
        # 名前空間ありの場合
        for trkpt in root.findall('.//gpx:trkpt', namespace):
            lat = trkpt.get('lat')
            lon = trkpt.get('lon')
            if lat and lon:
                coordinates.append((float(lat), float(lon)))
    else:
        # 名前空間なしの場合
        for trkpt in root.findall('.//trkpt'):
            lat = trkpt.get('lat')
            lon = trkpt.get('lon')
            if lat and lon:
                coordinates.append((float(lat), float(lon)))
    
    return coordinates


def _parse_tcx_track_coordinates(root: ET.Element) -> List[Tuple[float, float]]:
    """
    TCX (TrainingCenterDatabase)形式のXMLルート要素から座標リストを取得します。
    TCXでは座標が以下の構造で格納されています:
      <Trackpoint>
        <Position>
          <LatitudeDegrees>34.999...</LatitudeDegrees>
          <LongitudeDegrees>135.759...</LongitudeDegrees>
        </Position>
      </Trackpoint>
    """
    coordinates = []
    
    # TCX名前空間
    tcx_ns = 'http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2'
    namespace = {'tcx': tcx_ns}
    
    # 名前空間ありで検索
    trackpoints = root.findall('.//tcx:Trackpoint', namespace)
    
    if not trackpoints:
        # 名前空間なしでフォールバック
        trackpoints = root.findall('.//{%s}Trackpoint' % tcx_ns)
    
    if not trackpoints:
        # 名前空間完全なしでフォールバック
        trackpoints = root.findall('.//Trackpoint')
    
    for trkpt in trackpoints:
        # 名前空間ありで Position を検索
        position = trkpt.find('tcx:Position', namespace)
        if position is None:
            position = trkpt.find('{%s}Position' % tcx_ns)
        if position is None:
            position = trkpt.find('Position')
        
        if position is not None:
            # 注意: ElementTree の Element は子要素がない場合 falsy になるため、
            # or チェーンではなく is not None で明示的にチェックする必要があります。
            lat_elem = position.find('tcx:LatitudeDegrees', namespace)
            if lat_elem is None:
                lat_elem = position.find('{%s}LatitudeDegrees' % tcx_ns)
            if lat_elem is None:
                lat_elem = position.find('LatitudeDegrees')
            
            lon_elem = position.find('tcx:LongitudeDegrees', namespace)
            if lon_elem is None:
                lon_elem = position.find('{%s}LongitudeDegrees' % tcx_ns)
            if lon_elem is None:
                lon_elem = position.find('LongitudeDegrees')
            
            if lat_elem is not None and lon_elem is not None:
                if lat_elem.text and lon_elem.text:
                    coordinates.append((float(lat_elem.text), float(lon_elem.text)))
    
    return coordinates


def parse_gpx_date_range(gpx_file: Path) -> Optional[Tuple[datetime, datetime]]:
    """
    GPXまたはTCXファイルから日時範囲（開始日時と終了日時）を取得します。
    
    Garmin Connect APIはデフォルトでTCX形式をダウンロードするため、
    .gpx拡張子でもTCX形式の場合があります。両形式に対応しています。
    
    Args:
        gpx_file: GPXまたはTCXファイルのパス
        
    Returns:
        Optional[Tuple[datetime, datetime]]: (開始日時, 終了日時) または None
    """
    try:
        tree = ET.parse(gpx_file)
        root = tree.getroot()
        
        # ルート要素のタグから実際のフォーマットを判定します
        root_tag = root.tag.lower()
        
        if 'trainingcenterdatabase' in root_tag:
            timestamps = _parse_tcx_timestamps(root)
        else:
            timestamps = _parse_gpx_timestamps(root)
        
        if timestamps:
            return (min(timestamps), max(timestamps))
        
        return None
        
    except Exception as e:
        print(f"GPX日時パースエラー ({gpx_file.name}): {e}")
        return None


def _parse_gpx_timestamps(root: ET.Element) -> List[datetime]:
    """
    GPX形式のXMLルート要素からタイムスタンプリストを取得します。
    """
    timestamps = []
    
    # GPX名前空間の処理
    namespace = {'gpx': 'http://www.topografix.com/GPX/1/1'}
    
    # GPX 1.0の場合
    if 'http://www.topografix.com/GPX/1/0' in root.tag:
        namespace = {'gpx': 'http://www.topografix.com/GPX/1/0'}
    
    # 名前空間なしの場合も対応
    if not root.tag.startswith('{'):
        namespace = {}
    
    # トラックポイントの時刻を取得
    if namespace:
        # 名前空間ありの場合
        for trkpt in root.findall('.//gpx:trkpt', namespace):
            time_elem = trkpt.find('gpx:time', namespace)
            if time_elem is not None and time_elem.text:
                try:
                    # ISO 8601形式の日時をパース
                    dt = datetime.fromisoformat(time_elem.text.replace('Z', '+00:00'))
                    timestamps.append(dt)
                except:
                    pass
    else:
        # 名前空間なしの場合
        for trkpt in root.findall('.//trkpt'):
            time_elem = trkpt.find('time')
            if time_elem is not None and time_elem.text:
                try:
                    dt = datetime.fromisoformat(time_elem.text.replace('Z', '+00:00'))
                    timestamps.append(dt)
                except:
                    pass
    
    return timestamps


def _parse_tcx_timestamps(root: ET.Element) -> List[datetime]:
    """
    TCX (TrainingCenterDatabase)形式のXMLルート要素からタイムスタンプリストを取得します。
    TCXでは時刻が以下の構造で格納されています:
      <Trackpoint>
        <Time>2026-02-22T23:26:45.000Z</Time>
      </Trackpoint>
    """
    timestamps = []
    
    # TCX名前空間
    tcx_ns = 'http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2'
    namespace = {'tcx': tcx_ns}
    
    # 名前空間ありで検索
    trackpoints = root.findall('.//tcx:Trackpoint', namespace)
    
    if not trackpoints:
        trackpoints = root.findall('.//{%s}Trackpoint' % tcx_ns)
    
    if not trackpoints:
        trackpoints = root.findall('.//Trackpoint')
    
    for trkpt in trackpoints:
        # 注意: ElementTree の Element は子要素がない場合 falsy になるため、
        # or チェーンではなく is not None で明示的にチェックする必要があります。
        time_elem = trkpt.find('tcx:Time', namespace)
        if time_elem is None:
            time_elem = trkpt.find('{%s}Time' % tcx_ns)
        if time_elem is None:
            time_elem = trkpt.find('Time')
        if time_elem is not None and time_elem.text:
            try:
                dt = datetime.fromisoformat(time_elem.text.replace('Z', '+00:00'))
                timestamps.append(dt)
            except:
                pass
    
    return timestamps


def filter_gpx_by_photo_dates(gpx_files: List[Path], photo_dates: Set[date], camera_timezone_str: str = "Asia/Tokyo") -> List[Path]:
    """
    撮影日時に該当するGPXファイルをフィルタリングします。

    カメラのEXIF日時はタイムゾーン情報を持たない（offset-naive）ことが多いため、
    camera_timezone_str で指定されたタイムゾーンで解釈してGPXの日時（UTC aware）と比較します。
    
    Args:
        gpx_files: GPXファイルのリスト
        photo_dates: 撮影日のセット
        camera_timezone_str: カメラで設定されているタイムゾーン（IANA形式, デフォルト: "Asia/Tokyo"）
        
    Returns:
        List[Path]: 該当するGPXファイルのリスト
    """
    if not photo_dates:
        return []

    # カメラのタイムゾーンを取得します。
    camera_tz = ZoneInfo(camera_timezone_str)
    
    matching_gpx_files = []
    
    for gpx_file in gpx_files:
        date_range = parse_gpx_date_range(gpx_file)
        if date_range:
            start_date, end_date = date_range
            # GPXの日付範囲と撮影日が重なるかチェック
            for photo_date in photo_dates:
                # photo_dateをカメラのタイムゾーンで解釈したdatetimeに変換します（その日の0時と23時59分59秒）。
                # datetime.combine の tzinfo 引数で offset-aware な datetime を生成することで、
                # GPXから取得した UTC aware な datetime と安全に比較できます。
                photo_start = datetime.combine(photo_date, datetime.min.time(), tzinfo=camera_tz)
                photo_end = datetime.combine(photo_date, datetime.max.time(), tzinfo=camera_tz)
                
                # 日付範囲が重なるかチェック
                if not (photo_end < start_date or photo_start > end_date):
                    matching_gpx_files.append(gpx_file)
                    break  # このGPXファイルは該当するので次へ
    
    return matching_gpx_files


def sanitize_filename(name: str) -> str:
    """
    ファイル名に使えない文字を置換して安全にします。
    Windowsのファイル名で使えない文字を取り除きます。
    """
    forbidden = "\\/:*?\"<>|"
    for ch in forbidden:
        name = name.replace(ch, "_")
    return name


def extract_gps_from_exif(exif_data) -> Optional[tuple]:
    """
    EXIF情報からGPS座標を取得します。
    
    Returns:
        tuple: (緯度, 経度) または None
    """
    try:
        # GPSInfo (タグ34853)
        gps_info = exif_data.get(34853)
        if not gps_info:
            return None
        
        # 緯度・経度の取得
        gps_latitude = gps_info.get(2)  # GPSLatitude
        gps_latitude_ref = gps_info.get(1)  # GPSLatitudeRef (N/S)
        gps_longitude = gps_info.get(4)  # GPSLongitude
        gps_longitude_ref = gps_info.get(3)  # GPSLongitudeRef (E/W)
        
        if not all([gps_latitude, gps_latitude_ref, gps_longitude, gps_longitude_ref]):
            return None
        
        # 度分秒から十進数に変換
        def convert_to_degrees(value):
            d, m, s = value
            return d + (m / 60.0) + (s / 3600.0)
        
        lat = convert_to_degrees(gps_latitude)
        if gps_latitude_ref == 'S':
            lat = -lat
        
        lon = convert_to_degrees(gps_longitude)
        if gps_longitude_ref == 'W':
            lon = -lon
        
        return (lat, lon)
    except Exception:
        return None


# -----------------------------
# 設定ファイルの読み書き
# -----------------------------


class SettingsManager:
    """
    設定の読み書きを担当するクラスです。
    config.jsonにパスやユーザー情報などを保存します。
    デフォルトの保存先: ~/AppData/Local/GeotagPhoto/config.json
    設定タブでcache_dirを変更した場合、そちらに移動されます。
    """

    CONFIG_FILE = "config.json"

    @classmethod
    def _get_default_dir(cls) -> Path:
        """デフォルトの設定ファイル保存ディレクトリを返します。"""
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "GeotagPhoto"

    @classmethod
    def _get_app_dir(cls) -> Path:
        """アプリケーション(EXE/スクリプト)の配置ディレクトリを返します。"""
        if getattr(sys, 'frozen', False):
            return Path(sys.executable).parent
        return Path(__file__).resolve().parent

    @classmethod
    def get_config_path(cls) -> Path:
        """
        設定ファイルのパスを取得します。
        1. デフォルトディレクトリ(AppData/Local/GeotagPhoto)のconfig.jsonを確認
        2. その中にcache_dirが指定されていればそちらのconfig.jsonを返す
        3. デフォルトディレクトリにもなければアプリ配置ディレクトリの旧config.jsonを確認（後方互換）
        """
        default_dir = cls._get_default_dir()
        default_config = default_dir / cls.CONFIG_FILE

        # デフォルトディレクトリのconfig.jsonからcache_dirを読む
        if default_config.exists():
            try:
                with open(default_config, "r", encoding="utf-8") as file:
                    temp_settings = json.load(file)
                    cache_dir = temp_settings.get("cache_dir")
                    if cache_dir:
                        cache_path = Path(cache_dir)
                        cache_config = cache_path / cls.CONFIG_FILE
                        if cache_config.exists():
                            return cache_config
            except Exception:
                pass
            return default_config

        # 後方互換: アプリ配置ディレクトリの旧config.jsonを確認
        app_config = cls._get_app_dir() / cls.CONFIG_FILE
        if app_config.exists():
            try:
                with open(app_config, "r", encoding="utf-8") as file:
                    temp_settings = json.load(file)
                    cache_dir = temp_settings.get("cache_dir")
                    if cache_dir:
                        cache_path = Path(cache_dir)
                        cache_config = cache_path / cls.CONFIG_FILE
                        if cache_config.exists():
                            return cache_config
            except Exception:
                pass
            return app_config

        # どこにも存在しない場合はデフォルトディレクトリに新規作成用パスを返す
        default_dir.mkdir(parents=True, exist_ok=True)
        return default_config

    @classmethod
    def load(cls) -> dict:
        """
        設定ファイルがあれば読み込み、なければ空の辞書を返します。
        """
        config_path = cls.get_config_path()
        
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as file:
                settings = json.load(file)
                # デフォルト拡張子を追加
                if "custom_extensions" not in settings:
                    settings["custom_extensions"] = ".jpg,.jpeg,.png,.tif,.tiff,.heic,.cr2,.cr3,.nef,.nrw,.arw,.raf,.orf,.rw2,.pef,.dng,.rwl,.mov,.mp4,.avi,.mts,.m2ts"
                # デフォルトの上書き設定を追加（デフォルト: false = 上書きしない）
                if "overwrite_existing_geotag" not in settings:
                    settings["overwrite_existing_geotag"] = False
                # デフォルトのExifTool並列ワーカー数を追加
                if "exiftool_max_workers" not in settings:
                    settings["exiftool_max_workers"] = 4
                return settings
        return {
            "custom_extensions": ".jpg,.jpeg,.png,.tif,.tiff,.heic,.cr2,.cr3,.nef,.nrw,.arw,.raf,.orf,.rw2,.pef,.dng,.rwl,.mov,.mp4,.avi,.mts,.m2ts",
            "overwrite_existing_geotag": False,
            "exiftool_max_workers": 4
        }

    @classmethod
    def save(cls, data: dict) -> None:
        """
        既存設定に追記する形で保存します。
        cache_dirが変更された場合、設定ファイルを新しい場所に移動します。
        """
        old_config_path = cls.get_config_path()
        settings = cls.load()
        old_cache_dir = settings.get("cache_dir")
        settings.update(data)
        new_cache_dir = settings.get("cache_dir")

        # cache_dir が変更された場合、新しい場所に保存
        if new_cache_dir and new_cache_dir != old_cache_dir:
            new_dir = Path(new_cache_dir)
            new_dir.mkdir(parents=True, exist_ok=True)
            new_config_path = new_dir / cls.CONFIG_FILE
            with open(new_config_path, "w", encoding="utf-8") as file:
                json.dump(settings, file, indent=4, ensure_ascii=False)
            # デフォルトディレクトリにcache_dirへのポインタを残す
            default_dir = cls._get_default_dir()
            default_dir.mkdir(parents=True, exist_ok=True)
            pointer = {"cache_dir": new_cache_dir}
            with open(default_dir / cls.CONFIG_FILE, "w", encoding="utf-8") as file:
                json.dump(pointer, file, indent=4, ensure_ascii=False)
            # 旧config(デフォルトディレクトリ以外)が残っていれば削除
            if old_config_path.exists() and old_config_path != new_config_path and old_config_path.parent != default_dir:
                try:
                    old_config_path.unlink()
                except OSError:
                    pass
        else:
            # cache_dir未変更の場合は現在のパスに保存
            config_path = cls.get_config_path()
            config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as file:
                json.dump(settings, file, indent=4, ensure_ascii=False)


# -----------------------------
# Garminダウンロード処理
# -----------------------------


def _get_garmin_tokenstore_dir() -> Path:
    """
    Garminセッショントークンの保存先ディレクトリを取得します。
    キャッシュディレクトリ内の garmin_tokens サブフォルダを使用します。
    """
    settings = SettingsManager.load()
    cache_dir = settings.get("cache_dir", str(SettingsManager._get_default_dir()))
    token_dir = Path(cache_dir) / "garmin_tokens"
    token_dir.mkdir(parents=True, exist_ok=True)
    return token_dir


def _is_rate_limit_error(error: Exception) -> bool:
    """429レート制限エラーかどうかを判定します。例外チェーン全体を確認します。"""
    if isinstance(error, GarminConnectTooManyRequestsError):
        return True
    # 例外メッセージとチェーン先を両方チェック
    err = error
    while err is not None:
        error_str = str(err)
        if "429" in error_str or "Too Many Requests" in error_str:
            return True
        if isinstance(err, GarminConnectTooManyRequestsError):
            return True
        err = getattr(err, "__cause__", None)
    return False


class _GarminLogHandler(logging.Handler):
    """garminconnectライブラリのログをコールバックに転送するハンドラです。"""
    def __init__(self, callback: Callable[[str], None]):
        super().__init__()
        self.callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.callback(f"  [{record.levelname}] {msg}")
        except Exception:
            pass


def _garmin_login_with_retry(
    email: str,
    password: str,
    tokenstore_dir: Path,
    tokenstore_path: str,
    log_callback: Optional[Callable[[str], None]] = None,
    max_retries: int = 1,
) -> tuple:
    """
    Garmin Connectにログインします。

    garminconnect ライブラリ v0.3.1 は内部で最大8以上のPOSTリクエストを
    送信するため（portal×5impersonation + portal+requests + mobile×2）、
    外側でのリトライは原則1回のみとします。
    429エラーはGarminバックエンドのIPレベル制限（24時間以上持続する場合あり）
    であり、短時間の待機後にリトライしても解決しません。

    Returns:
        tuple: (Garmin APIインスタンス, ログイン方法の文字列)
    """
    # garminconnect ライブラリのログをUIに転送
    garmin_logger = logging.getLogger("garminconnect.client")
    log_handler = None
    if log_callback:
        log_handler = _GarminLogHandler(log_callback)
        log_handler.setLevel(logging.WARNING)
        garmin_logger.addHandler(log_handler)
        garmin_logger.setLevel(logging.DEBUG)

    # garminconnect ライブラリは garmin_tokens.json に保存します
    token_file = tokenstore_dir / "garmin_tokens.json"
    last_error = None

    try:
        for attempt in range(max_retries):
            try:
                api = Garmin(email, password)

                if token_file.exists():
                    if log_callback:
                        log_callback("トークンキャッシュからログインを試行します...")
                    api.login(tokenstore=tokenstore_path)
                    login_method = "トークンキャッシュ"
                else:
                    if log_callback:
                        log_callback("クレデンシャルでログインを試行します...")
                    api.login()
                    login_method = "クレデンシャル"

                # ログイン成功後、トークンをキャッシュに保存します。
                try:
                    api.client.dump(tokenstore_path)
                except Exception:
                    pass

                return api, login_method

            except GarminConnectAuthenticationError as e:
                if not _is_rate_limit_error(e):
                    raise  # パスワード不一致等は即座にエラー
                last_error = e

            except Exception as e:
                if not _is_rate_limit_error(e):
                    raise  # 429以外のエラーはそのまま投げる
                last_error = e

            # --- 429系エラーのハンドリング ---
            # 429はGarminバックエンドのIPレベル制限です。
            # トークンの問題ではないため、キャッシュは削除しません。
            # 短時間のリトライは無意味（制限は数時間〜24時間以上持続）なので、
            # ユーザーに状況を通知して終了します。
            if log_callback:
                log_callback(
                    "⚠ Garminサーバーからレート制限（429）を受けました。"
                )
                log_callback(
                    "  これはIPアドレスベースの制限で、"
                    "解除まで数時間〜24時間かかる場合があります。"
                )
                log_callback(
                    "  しばらく時間をおいてから再試行するか、"
                    "別のネットワーク（モバイル回線等）からお試しください。"
                )

        # 全リトライ失敗
        raise last_error

    finally:
        # ログハンドラをクリーンアップ
        if log_handler:
            garmin_logger.removeHandler(log_handler)


def download_garmin_activities_gpx(
    target_dates: List[date],
    output_dir: Path,
    email: str,
    password: str,
    log_callback: Optional[Callable[[str], None]] = None,
    download_format: str = "gpx",
) -> List[Path]:
    """
    指定された日付のGarminアクティビティを取得し、GPXまたはTCXを保存します。

    log_callbackには画面表示用のログ出力関数を渡します。
    download_format: "gpx" または "tcx" を指定します（デフォルト: "gpx"）。
    
    セッショントークンをキャッシュし、毎回のログインを避けることで
    Garmin SSO の 429 Too Many Requests エラーを防止します。
    
    Returns:
        ダウンロードされたファイルのパスリスト
    """
    # 出力先が存在しない場合は作成しておきます。
    output_dir.mkdir(parents=True, exist_ok=True)
    
    downloaded_files: List[Path] = []
    tokenstore_dir = _get_garmin_tokenstore_dir()
    tokenstore_path = str(tokenstore_dir)

    try:
        # Garmin ConnectのAPIクライアントを初期化し、ログインします。
        # 429エラー時は指数バックオフでリトライします。
        api, login_method = _garmin_login_with_retry(
            email, password, tokenstore_dir, tokenstore_path, log_callback
        )

        if log_callback:
            log_callback(f"'{email}' でログインに成功しました。（{login_method}）")

        for target_date in target_dates:
            if log_callback:
                log_callback(f"{target_date} のアクティビティを取得します...")

            # 指定日のアクティビティを取得します。
            activities = api.get_activities_by_date(
                target_date.strftime("%Y-%m-%d"),
                target_date.strftime("%Y-%m-%d"),
            )

            if not activities:
                if log_callback:
                    log_callback(f"{target_date} はアクティビティなし。")
                continue

            # 取得したアクティビティを順番に保存します。
            for index, activity in enumerate(activities):
                activity_id = activity.get("activityId")
                activity_name = sanitize_filename(activity.get("activityName", "activity"))
                
                # ダウンロード形式に応じてフォーマットと拡張子を決定します。
                if download_format == "tcx":
                    dl_fmt = Garmin.ActivityDownloadFormat.TCX
                    ext = ".tcx"
                else:
                    # デフォルトはGPX形式です。
                    # Garmin APIのデフォルトはTCXのため、明示的に指定します。
                    dl_fmt = Garmin.ActivityDownloadFormat.GPX
                    ext = ".gpx"
                
                file_name = (
                    f"activity_{target_date.strftime('%Y-%m-%d')}_"
                    f"{index + 1}_{activity_name}_{activity_id}{ext}"
                )
                file_path = output_dir / file_name

                try:
                    gpx_data = api.download_activity(activity_id, dl_fmt=dl_fmt)
                    with open(file_path, "wb") as file:
                        file.write(gpx_data)
                    downloaded_files.append(file_path)
                    if log_callback:
                        log_callback(f"✓ {file_name} を保存しました。")
                except Exception as error:
                    if log_callback:
                        log_callback(f"✗ {file_name} の保存に失敗: {error}")

    except GarminConnectTooManyRequestsError as error:
        if log_callback:
            log_callback("⚠ Garminサーバーからレート制限（429）を受けました。")
            log_callback("  IPアドレスベースの制限のため、解除まで数時間〜24時間かかる場合があります。")
            log_callback("  しばらく時間をおくか、別のネットワークから再試行してください。")
        raise
    except GarminConnectAuthenticationError as error:
        if _is_rate_limit_error(error):
            # 429が認証エラーとしてラップされた場合はトークンを削除しない
            if log_callback:
                log_callback("⚠ Garminサーバーからレート制限（429）を受けました。")
                log_callback("  IPアドレスベースの制限のため、解除まで数時間〜24時間かかる場合があります。")
                log_callback("  しばらく時間をおくか、別のネットワークから再試行してください。")
        else:
            # パスワード不一致等の認証エラー時のみトークンを削除
            try:
                for token_file in tokenstore_dir.glob("*.json"):
                    token_file.unlink()
            except Exception:
                pass
            if log_callback:
                log_callback(f"認証エラー: {error}")
        raise
    except GarminConnectConnectionError as error:
        # 接続エラー（429ラップ含む）の場合はトークンを削除しない
        if _is_rate_limit_error(error):
            if log_callback:
                log_callback("⚠ Garminサーバーからレート制限（429）を受けました。")
                log_callback("  IPアドレスベースの制限のため、解除まで数時間〜24時間かかる場合があります。")
                log_callback("  しばらく時間をおくか、別のネットワークから再試行してください。")
        else:
            if log_callback:
                log_callback(f"Garmin接続エラー: {error}")
        raise
    except Exception as error:
        if _is_rate_limit_error(error):
            if log_callback:
                log_callback("⚠ Garminサーバーからレート制限（429）を受けました。")
                log_callback("  IPアドレスベースの制限のため、解除まで数時間〜24時間かかる場合があります。")
                log_callback("  しばらく時間をおくか、別のネットワークから再試行してください。")
        else:
            if log_callback:
                log_callback(f"予期せぬエラー: {error}")
        raise
    
    return downloaded_files


# -----------------------------
# ジオタギング処理
# -----------------------------


@dataclass
class GeotagLogEntry:
    """
    ログ画面に表示するためのデータをまとめたクラスです。
    """

    filename: str
    datetime_original: Optional[datetime]
    gps_latitude: Optional[str]
    gps_longitude: Optional[str]

    def status_text(self) -> str:
        """
        GPS情報の有無を人間が読みやすい形で返します。
        """
        if self.gps_latitude and self.gps_longitude:
            return "付与あり"
        return "付与なし"


def _run_exiftool_argfile(exiftool_path: str, args: List[str], files: List[Path]) -> subprocess.CompletedProcess:
    """
    ExifToolを引数ファイル(-@)経由で実行します。
    Windowsのコマンドライン長制限を回避するため、ファイルパスを一時ファイルに書き出して渡します。
    ExifToolの終了コード1（軽微な警告）は正常扱いとし、2以上のみ例外を送出します。
    """
    argfile_path = None
    try:
        # utf-8-sig: BOM付きUTF-8で書き出す。ExifToolはBOM付きargfileをUTF-8として認識する
        # （BOMなしの場合、Windowsではシステムコードページ=CP932で読まれ日本語パスが文字化けする）
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8-sig') as f:
            for arg in args:
                f.write(f'{arg}\n')
            for path in files:
                f.write(f'{path}\n')
            argfile_path = f.name

        result = subprocess.run(
            [exiftool_path, '-@', argfile_path],
            capture_output=True, encoding='utf-8', errors='ignore'
        )
        # ExifTool終了コード: 0=成功, 1=軽微な警告(正常扱い), 2=致命的エラー
        if result.returncode >= 2:
            raise subprocess.CalledProcessError(
                result.returncode, [exiftool_path, '-@', argfile_path],
                output=result.stdout, stderr=result.stderr
            )
        return result
    finally:
        if argfile_path:
            Path(argfile_path).unlink(missing_ok=True)


def filter_files_without_gps(exiftool_path: str, files: List[Path]) -> tuple[List[Path], List[Path]]:
    """
    ファイルをGPS情報の有無で分類します。
    
    Returns:
        tuple: (GPS情報がないファイルのリスト, GPS情報があるファイルのリスト)
    """
    if not files:
        return [], []
    
    # ExifToolでGPS情報を確認（-@引数ファイル経由）
    try:
        result = _run_exiftool_argfile(
            exiftool_path,
            ['-json', '-GPSLatitude', '-GPSLongitude', '-FileName'],
            files
        )
        if not result.stdout or result.stdout.strip() == "":
            return files, []  # 情報が取得できない場合は全てGPS情報なしとして扱う
        
        data = json.loads(result.stdout)
        
        # ファイル名をキーとした辞書を作成
        gps_info = {}
        for item in data:
            filename = item.get("FileName", "")
            gps_lat = item.get("GPSLatitude")
            gps_lon = item.get("GPSLongitude")
            gps_info[filename] = (gps_lat is not None and gps_lon is not None)
        
        # ファイルを分類
        files_without_gps = []
        files_with_gps = []
        
        for file_path in files:
            has_gps = gps_info.get(file_path.name, False)
            if has_gps:
                files_with_gps.append(file_path)
            else:
                files_without_gps.append(file_path)
        
        return files_without_gps, files_with_gps
    except Exception:
        # エラーの場合は全てGPS情報なしとして扱う
        return files, []


def run_exiftool_geotag(exiftool_path: str, gpx_file: Path, dest_dir: Path, file_extensions: set, overwrite_existing: bool = False, max_workers: int = 4) -> tuple[int, int]:
    """
    ExifToolでGPX/TCXを使ってジオタギングを行います。
    並列処理により複数のexiftoolプロセスを実行して高速化します。
    
    Args:
        exiftool_path: ExifToolの実行ファイルパス
        gpx_file: GPX/TCXファイルのパス
        dest_dir: 処理対象ディレクトリ
        file_extensions: 処理対象の拡張子セット
        overwrite_existing: 既にGPS情報があるファイルも上書きするかどうか（デフォルト: False）
        max_workers: 並列実行するワーカー数（デフォルト: 4）
    
    Returns:
        tuple: (ジオタグを付与したファイル数, スキップしたファイル数)
    """
    # 処理対象のファイルを収集（custom_extsにマッチするファイルのみ）
    all_files = [
        path
        for path in dest_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in file_extensions
    ]
    
    if not all_files:
        # 処理対象がない場合は何もしない
        return 0, 0
    
    # 上書き設定に応じて処理対象を決定
    if overwrite_existing:
        # 上書きする場合：全ファイルを処理対象とする
        files_to_process = all_files
        skipped_count = 0
    else:
        # 上書きしない場合：GPS情報がないファイルのみを処理対象とする
        files_without_gps, files_with_gps = filter_files_without_gps(exiftool_path, all_files)
        files_to_process = files_without_gps
        skipped_count = len(files_with_gps)
    
    if not files_to_process:
        # 処理対象がない場合は何もしない
        return 0, skipped_count
    
    # ファイルをバッチに分割して並列処理
    # ワーカー数が1の場合、または処理対象ファイルが少ない場合は並列化しない
    if max_workers <= 1 or len(files_to_process) <= 10:
        # 通常の処理（並列化なし）
        # -@引数ファイル経由で実行（Windowsコマンドライン長制限回避）
        _run_exiftool_argfile(
            exiftool_path,
            ['-overwrite_original', '-geotag', str(gpx_file)],
            files_to_process
        )
        
        return len(files_to_process), skipped_count
    
    # 並列処理: ファイルをバッチに分割
    # 各バッチには適度な数のファイルを割り当て（最小5ファイル/バッチ）
    batch_size = max(5, len(files_to_process) // max_workers)
    batches = [files_to_process[i:i + batch_size] for i in range(0, len(files_to_process), batch_size)]
    
    def process_batch(batch_files: List[Path]) -> int:
        """
        ファイルのバッチに対してexiftoolを実行します。
        
        Returns:
            int: 処理したファイル数
        """
        try:
            _run_exiftool_argfile(
                exiftool_path,
                ['-overwrite_original', '-geotag', str(gpx_file)],
                batch_files
            )
            return len(batch_files)
        except subprocess.CalledProcessError as e:
            # exiftoolのエラー出力を表示（コマンド全体ではなく原因を表示）
            stderr_msg = e.stderr.strip() if e.stderr else f"exit code {e.returncode}"
            print(f"⚠ バッチ処理でエラー: {stderr_msg}")
            return 0
        except Exception as e:
            print(f"⚠ バッチ処理でエラー: {e}")
            return 0
    
    # ThreadPoolExecutorで並列実行
    # ProcessPoolExecutorではなくThreadPoolExecutorを使用（I/O待ちが多いため）
    processed_count = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 各バッチを並列実行
        results = executor.map(process_batch, batches)
        processed_count = sum(results)
    
    return processed_count, skipped_count


def collect_exif_log(exiftool_path: str, dest_dir: Path, file_extensions: set) -> List[GeotagLogEntry]:
    """
    ExifToolのJSON出力をパースしてログ一覧を作成します。
    custom_extsで指定されたファイルのみを処理対象とします。
    """
    # 処理対象のファイルを収集
    files_to_process = [
        path
        for path in dest_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in file_extensions
    ]
    
    if not files_to_process:
        return []
    
    # ExifToolの結果をJSON文字列として取得します。
    # -@引数ファイル経由で実行（Windowsコマンドライン長制限回避）
    result = _run_exiftool_argfile(
        exiftool_path,
        ['-json', '-DateTimeOriginal', '-GPSLatitude', '-GPSLongitude', '-FileName'],
        files_to_process
    )
    
    # stdoutが空の場合のハンドリング
    if not result.stdout or result.stdout.strip() == "":
        return []
    
    data = json.loads(result.stdout)

    entries: List[GeotagLogEntry] = []
    for item in data:
        filename = item.get("FileName", "")
        datetime_str = item.get("DateTimeOriginal")
        gps_lat = item.get("GPSLatitude")
        gps_lon = item.get("GPSLongitude")

        # DateTimeOriginalが文字列の場合はdatetimeに変換します。
        parsed_datetime = None
        if datetime_str:
            try:
                parsed_datetime = datetime.strptime(datetime_str, "%Y:%m:%d %H:%M:%S")
            except ValueError:
                # 変換できない場合はそのままNoneにします。
                parsed_datetime = None

        entries.append(
            GeotagLogEntry(
                filename=filename,
                datetime_original=parsed_datetime,
                gps_latitude=gps_lat,
                gps_longitude=gps_lon,
            )
        )

    # 撮影日時でソートし、日時がないものは最後にまとめます。
    entries.sort(
        key=lambda entry: (
            entry.datetime_original is None,
            entry.datetime_original or datetime.max,
        )
    )

    return entries


# -----------------------------
# UI (ポップアップやメイン画面)
# -----------------------------


class DownloadLogPopup(ctk.CTkToplevel):
    """
    ポップアップ: ダウンロードのログを表示する画面です。
    """

    def __init__(self, master: ctk.CTk, start_date: date, end_date: Optional[date], selected_dates: Optional[List[date]] = None):
        super().__init__(master)
        self.title("ダウンロード中...")
        self.geometry("600x300")
        self.transient(master)

        self.start_date = start_date
        self.end_date = end_date or start_date
        self.selected_dates = selected_dates  # スキャンで取得した日付リスト

        # 下のウィンドウの操作をロックします。
        self.grab_set()

        # タイトルラベル
        ctk.CTkLabel(self, text="ダウンロード実行ログ", font=("Yu Gothic UI", 14)).pack(pady=10)

        # ログテキストボックス
        self.log_textbox = ctk.CTkTextbox(self, width=580, height=330)
        self.log_textbox.pack(padx=10, pady=10, fill="both", expand=True)

        # ボタンフレーム
        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.pack(pady=10)

        self.close_button = ctk.CTkButton(button_frame, text="閉じる", command=self.destroy, state="disabled")
        self.close_button.pack(side="left", padx=6)

        # 別スレッドでダウンロードを開始します。
        threading.Thread(target=self.start_download, daemon=True).start()

    def start_download(self) -> None:
        """
        ダウンロード処理を別スレッドで開始します。
        """
        # 設定から保存先を取得します。
        settings = SettingsManager.load()
        gpx_dir = settings.get("gpx_dir")
        if not gpx_dir:
            self.update_log("エラー: GPX保存先が設定されていません。")
            self.after(0, lambda: self.close_button.configure(state="normal"))
            return

        # Garminの認証情報を取得します。
        email = settings.get("email", "")
        password = decode_password(settings.get("password_encoded", ""))
        if not email or not password:
            self.update_log("エラー: Garminのユーザー名とパスワードが設定されていません。")
            self.after(0, lambda: self.close_button.configure(state="normal"))
            return

        # 日付リストを作成します。
        if self.selected_dates:
            # スキャンで取得した日付を使用
            target_dates = self.selected_dates
        else:
            # 範囲指定の日付を使用
            target_dates = []
            current_date = self.start_date
            while current_date <= self.end_date:
                target_dates.append(current_date)
                current_date += timedelta(days=1)

        # ダウンロード形式を設定から取得します（デフォルト: gpx）。
        download_format = settings.get("activity_download_format", "gpx")
        
        # ダウンロード処理を実行します。
        self.download_worker(target_dates, Path(gpx_dir), email, password, download_format)

    def download_worker(
        self, target_dates: List[date], gpx_dir: Path, email: str, password: str,
        download_format: str = "gpx"
    ) -> None:
        """
        実際のダウンロード処理を行います。
        """
        def update_log_callback(message: str) -> None:
            self.update_log(message)

        fmt_label = "GPX" if download_format == "gpx" else "TCX"
        try:
            self.update_log(f"ダウンロード開始...（形式: {fmt_label}）")
            downloaded_files = download_garmin_activities_gpx(
                target_dates,
                gpx_dir,
                email,
                password,
                log_callback=update_log_callback,
                download_format=download_format,
            )
            # ダウンロードされたファイルをMainAppに保存
            if isinstance(self.master, MainApp):
                self.master.downloaded_gpx_files = downloaded_files
            self.update_log(f"ダウンロード完了。{len(downloaded_files)}件のファイルを保存しました。")
            self.update_log("━" * 40)
            self.update_log("✅ ダウンロードが完了しました。このポップアップを閉じてから、「取り込み」を実行してください。")
        except Exception as error:
            error_str = str(error)
            if "429" not in error_str and "Too Many Requests" not in error_str:
                self.update_log(f"ダウンロード中にエラーが発生しました: {error}")
            self.update_log("━" * 40)
            self.update_log("❌ ダウンロードに失敗しました。エラー内容を確認してください。")
        finally:
            self.after(0, lambda: self.close_button.configure(state="normal"))

    def update_log(self, message: str) -> None:
        """
        ログを表示します。
        """
        # コンソールに出力
        print(message)
        # UI更新
        def update_ui() -> None:
            self.log_textbox.insert("end", message + "\n")
            self.log_textbox.see("end")

        self.after(0, update_ui)


class ProcessingPopup(ctk.CTkToplevel):
    """
    ポップアップ: 取り込み処理を行う画面です。
    """

    def __init__(self, master: ctk.CTk, source_entry, dest_entry, gpx_entry):
        super().__init__(master)
        self.title("取り込み処理")
        self.geometry("1100x650")
        self.transient(master)

        self.source_entry = source_entry
        self.dest_entry = dest_entry
        self.gpx_entry = gpx_entry
        self.is_processing = False
        self.master_app = master

        # 下のウィンドウの操作をロックします。
        self.grab_set()

        # タイトル
        ctk.CTkLabel(self, text="取り込み処理中...", font=("Yu Gothic UI", 16, "bold")).pack(pady=10)

        # 進捗ラベル（パーセンテージ表示）
        self.progress_label = ctk.CTkLabel(self, text="初期化中... (0%)", font=("Yu Gothic UI", 14))
        self.progress_label.pack(pady=6)

        # 進捗バー
        self.progress_bar = ctk.CTkProgressBar(self, width=700)
        self.progress_bar.set(0)
        self.progress_bar.pack(pady=10, padx=20)

        # コンソールログ（タイトルラベルは処理完了後に「処理ログ（完了）」へ変更します）
        self.log_title_label = ctk.CTkLabel(self, text="処理ログ:", font=("Yu Gothic UI", 12, "bold"))
        self.log_title_label.pack(pady=(10, 5), anchor="w", padx=20)
        # fill="both" + expand=True で残領域を使い切ることで、閉じるボタンが隠れなくなります。
        self.console_log = ctk.CTkTextbox(self, font=("Consolas", 10))
        self.console_log.pack(pady=5, padx=20, fill="both", expand=True)

        # 閉じるボタン（最初は無効・処理完了後に有効化）
        # command は close_popup に設定し、閉じると同時にGPX軌跡を再描画します。
        self.close_button = ctk.CTkButton(self, text="閉じる", command=self.close_popup, state="disabled", width=120, height=35)
        self.close_button.pack(pady=15)
        
        # 自動的に処理を開始
        self.after(500, self.start_processing)

    def close_popup(self) -> None:
        """
        ポップアップを閉じます。
        同時に、マップ上のGPX軌跡を最新の状態で再描画します。
        grab_set() が解除された後に描画を行うことで確実に表示されます。
        """
        master = self.master_app
        # 描画するGPXファイルを決定（matching → downloaded → None の優先順）
        if isinstance(master, MainApp):
            gpx_files_to_draw = (
                master.matching_gpx_files
                or master.downloaded_gpx_files
                or None
            )
        else:
            gpx_files_to_draw = None

        self.destroy()

        # grab解放後に少し遅延させてから描画（200ms）
        if isinstance(master, MainApp):
            master.after(200, lambda: master.update_gpx_paths(gpx_files_to_draw))

    def start_processing(self) -> None:
        """
        取り込み処理をスレッドで開始します。
        """
        if self.is_processing:
            return

        self.is_processing = True
        threading.Thread(target=self.process_logic, daemon=True).start()
    
    def log_message(self, message: str, level: str = "INFO") -> None:
        """
        コンソールログにメッセージを追加します。
        """
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_line = f"[{timestamp}] {level}: {message}\n"
        
        def update_log():
            self.console_log.insert("end", log_line)
            self.console_log.see("end")
        
        self.after(0, update_log)

    def process_logic(self) -> None:
        """
        実際の取り込み処理を実行します。
        """
        try:
            source_dir = Path(self.source_entry.get())
            dest_dir = Path(self.dest_entry.get())
            gpx_dir = Path(self.gpx_entry.get())

            self.log_message("処理を開始します")

            if not source_dir.exists():
                self.log_message("読み込む画像のディレクトリが存在しません", "ERROR")
                self.is_processing = False
                self.after(0, lambda: self.close_button.configure(state="normal"))
                return
            if not dest_dir.exists():
                self.log_message(f"取り込み先ディレクトリを作成: {dest_dir}")
                dest_dir.mkdir(parents=True, exist_ok=True)
            if not gpx_dir.exists():
                self.log_message("位置情報ファイルの保存先が存在しません", "ERROR")
                self.is_processing = False
                self.after(0, lambda: self.close_button.configure(state="normal"))
                return

            # 1. 画像ファイルをコピーします（RAW、動画含む）
            self.log_message("画像・動画ファイルを検索中...")
            
            # カスタム拡張子を取得
            settings = SettingsManager.load()
            custom_exts = settings.get("custom_extensions", ".jpg,.jpeg,.png,.tif,.tiff,.heic,.cr2,.cr3,.nef,.nrw,.arw,.raf,.orf,.rw2,.pef,.dng,.rwl,.mov,.mp4,.avi,.mts,.m2ts")
            file_extensions = {ext.strip().lower() for ext in custom_exts.split(",") if ext.strip()}
            
            files_to_copy = [
                path
                for path in source_dir.rglob("*")
                if path.is_file() and path.suffix.lower() in file_extensions
            ]

            if not files_to_copy:
                self.log_message("コピー対象のファイルがありません", "WARNING")
                self.is_processing = False
                self.after(0, lambda: self.close_button.configure(state="normal"))
                return

            self.log_message(f"{len(files_to_copy)} 個のファイルを発見しました")
            self.update_progress("コピー中...", 0, 0)

            for index, file_path in enumerate(files_to_copy, start=1):
                progress = (index / len(files_to_copy)) * 0.3  # 0-30%
                percentage = int(progress * 100)
                self.update_progress(f"コピー中... ({index}/{len(files_to_copy)})", progress, percentage)
                try:
                    shutil.copy2(file_path, dest_dir / file_path.name)
                except Exception as e:
                    self.log_message(f"コピー失敗: {file_path.name} - {e}", "WARNING")

            self.log_message(f"コピー完了: {len(files_to_copy)} 個のファイル")

            # 2. GPXまたはTCXファイルを取得します。
            self.log_message("GPX/TCXファイルを検索中...")
            gpx_files = list_gpx_or_tcx_files(gpx_dir)
            if not gpx_files:
                self.log_message("GPX/TCXファイルが見つかりませんでした", "ERROR")
                self.is_processing = False
                self.after(0, lambda: self.close_button.configure(state="normal"))
                return

            gpx_file = gpx_files[0]
            self.log_message(f"GPXファイルを使用: {gpx_file.name}")

            # 3. ExifToolでジオタギングします。
            exiftool_path = find_exiftool()
            self.update_progress("ExifToolでジオタギング中...", 0.35, 35)
            self.log_message("ExifToolでジオタギングを実行中...")

            try:
                # 設定から上書き設定を取得（デフォルト: False）
                overwrite_existing = settings.get("overwrite_existing_geotag", False)
                # 設定から並列ワーカー数を取得（デフォルト: 4）
                max_workers = int(settings.get("exiftool_max_workers", 4))
                tagged_count, skipped_count = run_exiftool_geotag(exiftool_path, gpx_file, dest_dir, file_extensions, overwrite_existing, max_workers)
                if skipped_count > 0:
                    self.log_message(f"ExifToolジオタギング完了: {tagged_count}個に付与、{skipped_count}個はスキップ（既にGPS情報あり）")
                else:
                    self.log_message(f"ExifToolジオタギング完了: {tagged_count}個に付与")
            except subprocess.CalledProcessError as error:
                self.log_message(f"ExifToolの実行に失敗しました: {error}", "ERROR")
                self.is_processing = False
                self.after(0, lambda: self.close_button.configure(state="normal"))
                return
            except Exception as error:
                self.log_message(f"ExifToolエラー: {error}", "ERROR")
                self.is_processing = False
                self.after(0, lambda: self.close_button.configure(state="normal"))
                return
            
            # 3.5. ジオタグの有無でディレクトリ分けとリアルタイム地図更新
            self.update_progress("ファイルを整理中...", 0.5, 50)
            self.log_message("ジオタグの有無でファイルを分類中...")
            photo_dates = self.organize_and_update_map(dest_dir, file_extensions, exiftool_path)
            
            # 3.6. 撮影日時に該当するGPXファイルを特定
            self.update_progress("該当GPXファイルを検索中...", 0.6, 60)
            if photo_dates:
                self.log_message(f"{len(photo_dates)}日分の撮影日を検出しました")
                # 診断: 検出した撮影日を表示
                for pd in sorted(photo_dates):
                    self.log_message(f"  [診断] 撮影日: {pd}")
                self.log_message("撮影日時に該当するGPXファイルを検索中...")
                all_gpx_files = list_gpx_or_tcx_files(gpx_dir)
                self.log_message(f"  [診断] gpx_dirのファイル数: {len(all_gpx_files)}")
                # 設定からカメラのタイムゾーンを取得して渡します。
                camera_tz_str = settings.get("camera_timezone", "Asia/Tokyo")
                self.log_message(f"  [診断] カメラのタイムゾーン: {camera_tz_str}")
                # 各GPXファイルの日時範囲を診断表示
                for gf in all_gpx_files:
                    dr = parse_gpx_date_range(gf)
                    self.log_message(f"  [診断] {gf.name}: 日時範囲={dr}")
                matching_gpx = filter_gpx_by_photo_dates(all_gpx_files, photo_dates, camera_tz_str)
                
                if matching_gpx:
                    self.log_message(f"{len(matching_gpx)}件の該当GPXファイルを発見しました")
                    for gpx in matching_gpx:
                        self.log_message(f"  - {gpx.name}")
                    # MainAppに該当GPXファイルを保存
                    if isinstance(self.master_app, MainApp):
                        self.master_app.matching_gpx_files = matching_gpx
                else:
                    self.log_message("撮影日時に該当するGPXファイルが見つかりませんでした", "WARNING")
                    # フォールバック: ダウンロード済みGPXがあればそちらを使用
                    if isinstance(self.master_app, MainApp) and self.master_app.downloaded_gpx_files:
                        self.master_app.matching_gpx_files = self.master_app.downloaded_gpx_files
                        self.log_message(
                            f"フォールバック: ダウンロード済みGPX {len(self.master_app.downloaded_gpx_files)} 件を軌跡描画に使用します",
                            "INFO"
                        )
                    else:
                        # さらにフォールバック: GPXディレクトリの最新ファイルを使用
                        gpx_dir_str = SettingsManager.load().get("gpx_dir", "")
                        if gpx_dir_str:
                            fallback_files = list_gpx_or_tcx_files(Path(gpx_dir_str))[:5]
                            if fallback_files and isinstance(self.master_app, MainApp):
                                self.master_app.matching_gpx_files = fallback_files
                                self.log_message(
                                    f"フォールバック: GPXディレクトリの最新 {len(fallback_files)} 件を軌跡描画に使用します",
                                    "INFO"
                                )
            else:
                self.log_message(f"撮影日時情報が取得できませんでした（photo_dates={photo_dates}）", "WARNING")

            # 4. ログを収集します。
            self.update_progress("ログ収集中...", 0.90, 90)
            self.log_message("EXIFログを収集中...")
            try:
                log_entries = self.collect_organized_log(dest_dir, exiftool_path, file_extensions)
                self.log_message(f"{len(log_entries)} 個のログエントリを収集しました")
            except Exception as error:
                self.log_message(f"ログ収集エラー: {error}", "WARNING")
                log_entries = []

            # 5. GPX軌跡を描画します（撮影日時に該当するファイルのみ）
            self.update_progress("GPX軌跡を描画中...", 0.95, 95)
            if isinstance(self.master_app, MainApp) and self.master_app.matching_gpx_files:
                self.log_message(f"{len(self.master_app.matching_gpx_files)}件のGPX軌跡を描画中...")
                try:
                    # MainAppのupdate_gpx_paths()を呼び出し
                    self.master_app.after(0, lambda: self.master_app.update_gpx_paths(self.master_app.matching_gpx_files))
                    self.log_message("GPX軌跡の描画が完了しました")
                except Exception as error:
                    self.log_message(f"GPX軌跡描画エラー: {error}", "WARNING")
            else:
                self.log_message("撮影日時に該当するGPXファイルがありません（GPX軌跡は描画されません）", "INFO")

            self.update_progress("完了", 1.0, 100)
            self.log_message("処理が正常に完了しました")
            self.is_processing = False

            # 処理ログのタイトルを「処理ログ（完了）」へ変更します。
            self.after(0, lambda: self.log_title_label.configure(text="処理ログ（完了）:"))

            # 6. ログ画面を表示します。
            if log_entries:
                self.after(0, lambda: LogPopup(self, log_entries))
            self.after(0, lambda: self.close_button.configure(state="normal"))
            
        except Exception as e:
            self.log_message(f"予期せぬエラーが発生しました: {e}", "ERROR")
            self.is_processing = False
            self.after(0, lambda: self.close_button.configure(state="normal"))

    def organize_and_update_map(self, dest_dir: Path, file_extensions: set, exiftool_path: str) -> Set[date]:
        """
        ジオタグの有無でファイルを分類し、リアルタイムで地図を更新します。
        ExifToolを使用してすべてのファイル形式（RAW含む）に対応します。
        
        Returns:
            Set[date]: 撮影日のセット
        """
        with_geotag_dir = dest_dir / "with_geotag"
        without_geotag_dir = dest_dir / "without_geotag"
        
        with_count = 0
        without_count = 0
        photo_dates = set()  # 撮影日を収集
        
        # ExifToolでファイル情報を一括取得（-@引数ファイル経由）
        target_files = [
            path
            for path in dest_dir.iterdir()
            if path.is_file() and path.suffix.lower() in file_extensions
        ]
        
        if not target_files:
            self.log_message("分類対象のファイルがありません", "WARNING")
            return photo_dates
        
        try:
            result = _run_exiftool_argfile(
                exiftool_path,
                ['-json', '-DateTimeOriginal', '-GPSLatitude', '-GPSLongitude', '-FileName'],
                target_files
            )
            if not result.stdout or result.stdout.strip() == "":
                self.log_message("ExifToolからデータを取得できませんでした", "WARNING")
                return photo_dates
            
            data = json.loads(result.stdout)
            
            # ファイル名をキーとした辞書を作成
            exif_info = {}
            for item in data:
                filename = item.get("FileName", "")
                exif_info[filename] = {
                    "datetime": item.get("DateTimeOriginal"),
                    "gps_lat": item.get("GPSLatitude"),
                    "gps_lon": item.get("GPSLongitude")
                }
        except Exception as e:
            self.log_message(f"ExifTool実行エラー: {e}", "ERROR")
            return photo_dates
        
        # ファイルを分類
        for file_path in dest_dir.rglob("*"):
            # サブディレクトリ内のファイルはスキップ
            if file_path.parent != dest_dir:
                continue
            
            if file_path.is_file() and file_path.suffix.lower() in file_extensions:
                try:
                    file_info = exif_info.get(file_path.name, {})
                    
                    # 撮影日時を取得
                    date_str = file_info.get("datetime")
                    photo_date = "unknown_date"
                    photo_datetime_obj = None
                    
                    if date_str:
                        try:
                            photo_datetime_obj = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
                            photo_date = photo_datetime_obj.strftime("%Y-%m-%d")
                            # 撮影日を収集
                            photo_dates.add(photo_datetime_obj.date())
                        except ValueError:
                            photo_date = "unknown_date"
                    
                    # GPS情報を確認（緯度経度が両方存在する場合のみ有効）
                    gps_lat = file_info.get("gps_lat")
                    gps_lon = file_info.get("gps_lon")
                    has_geotag = gps_lat is not None and gps_lon is not None
                    
                    if has_geotag:
                        # ジオタグあり
                        target_dir = with_geotag_dir / photo_date
                        target_dir.mkdir(parents=True, exist_ok=True)
                        target_path = target_dir / file_path.name
                        shutil.move(str(file_path), str(target_path))
                        with_count += 1
                        
                        # GPS座標を数値に変換（ExifToolは度分秒形式で返す場合がある）
                        try:
                            if isinstance(gps_lat, str):
                                lat = self.parse_gps_coordinate(gps_lat)
                            else:
                                lat = float(gps_lat)
                            
                            if isinstance(gps_lon, str):
                                lon = self.parse_gps_coordinate(gps_lon)
                            else:
                                lon = float(gps_lon)
                        except:
                            lat = None
                            lon = None
                        
                        # 位置情報を収集（バッチ更新用）
                        if lat is not None and lon is not None:
                            photo_location = {
                                "file_name": file_path.name,
                                "file_path": str(target_path),
                                "lat": lat,
                                "lon": lon,
                                "date": photo_datetime_obj
                            }
                            
                            # 位置情報リストに追加（マーカーは最後にバッチ更新）
                            if isinstance(self.master_app, MainApp):
                                self.master_app.photo_locations.append(photo_location.copy())
                        
                        self.log_message(f"✓ {file_path.name} → with_geotag/{photo_date}/")
                    else:
                        # ジオタグなし
                        target_dir = without_geotag_dir / photo_date
                        target_dir.mkdir(parents=True, exist_ok=True)
                        target_path = target_dir / file_path.name
                        shutil.move(str(file_path), str(target_path))
                        without_count += 1
                        self.log_message(f"○ {file_path.name} → without_geotag/{photo_date}/")
                        
                except Exception as e:
                    self.log_message(f"処理失敗: {file_path.name} - {e}", "WARNING")
        
        self.log_message(f"分類完了: ジオタグあり={with_count}, なし={without_count}")
        
        # 分類完了後にマーカーをバッチ更新
        if isinstance(self.master_app, MainApp):
            self.master_app.after(0, self.master_app.update_map_markers)
        
        return photo_dates
    
    def parse_gps_coordinate(self, coord_str: str) -> float:
        """
        ExifToolのGPS座標文字列（度分秒形式など）を10進数に変換します。
        例: "35 deg 40' 52.32\" N" -> 35.681200
        """
        import re
        # deg分秒形式の解析
        match = re.match(r"(\d+) deg (\d+)' ([\d.]+)\" ([NSEW])", coord_str)
        if match:
            degrees = float(match.group(1))
            minutes = float(match.group(2))
            seconds = float(match.group(3))
            direction = match.group(4)
            
            decimal = degrees + (minutes / 60) + (seconds / 3600)
            
            if direction in ['S', 'W']:
                decimal = -decimal
            
            return decimal
        else:
            # すでに10進数形式の場合
            return float(coord_str)
    
    def collect_organized_log(self, dest_dir: Path, exiftool_path: str, file_extensions: set) -> List[GeotagLogEntry]:
        """
        整理後のディレクトリからログを収集します。
        """
        entries = []
        with_geotag_dir = dest_dir / "with_geotag"
        without_geotag_dir = dest_dir / "without_geotag"
        
        for directory in [with_geotag_dir, without_geotag_dir]:
            if directory.exists():
                try:
                    dir_entries = collect_exif_log(exiftool_path, directory, file_extensions)
                    entries.extend(dir_entries)
                except:
                    pass
        
        return entries
    
    def scan_geotagged_images(self, dest_dir: Path) -> None:
        """
        取り込み後の画像からGPS情報を取得して地図を更新します。
        """
        # カスタム拡張子を取得
        settings = SettingsManager.load()
        custom_exts = settings.get("custom_extensions", ".jpg,.jpeg,.png,.tif,.tiff,.heic,.cr2,.cr3,.nef,.nrw,.arw,.raf,.orf,.rw2,.pef,.dng,.rwl,.mov,.mp4,.avi,.mts,.m2ts")
        file_extensions = {ext.strip().lower() for ext in custom_exts.split(",") if ext.strip()}
        
        photo_locations = []
        
        for file_path in dest_dir.rglob("*"):
            if file_path.is_file() and file_path.suffix.lower() in file_extensions:
                try:
                    img = Image.open(file_path)
                    exif_data = img.getexif()
                    
                    if exif_data:
                        # GPS情報を取得
                        gps_coords = extract_gps_from_exif(exif_data)
                        if gps_coords:
                            # 撮影日時も取得
                            date_str = exif_data.get(36867) or exif_data.get(306)
                            photo_datetime = None
                            if date_str:
                                try:
                                    photo_datetime = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
                                except ValueError:
                                    pass
                            
                            photo_locations.append({
                                "file_name": file_path.name,
                                "file_path": str(file_path),
                                "lat": gps_coords[0],
                                "lon": gps_coords[1],
                                "date": photo_datetime
                            })
                except Exception:
                    # 読み込めないファイルはスキップ
                    pass
        
        # MainAppの地図を更新
        if isinstance(self.master, MainApp):
            self.master.photo_locations = photo_locations
            self.master.after(0, self.master.update_map_markers)

    def update_progress(self, message: str, value: float, percentage: int) -> None:
        """
        進捗表示を更新するためのヘルパー関数です。
        """
        def update() -> None:
            self.progress_label.configure(text=f"{message} ({percentage}%)")
            self.progress_bar.set(value)

        self.after(0, update)

    def show_message(self, title: str, message: str) -> None:
        """
        メッセージボックスを表示します。
        """
        self.after(0, lambda: messagebox.showinfo(title, message))


class LogPopup(ctk.CTkToplevel):
    """
    ポップアップ③: ジオタギング後のログを表示する画面です。
    """

    def __init__(self, master: ctk.CTk, entries: List[GeotagLogEntry]):
        super().__init__(master)
        self.title("処理ログ（完了）")
        self.geometry("700x500")
        self.transient(master)
        # 下のウィンドウの操作をロックします。
        self.grab_set()

        # テキスト表示用のウィジェットを用意します。
        textbox = ctk.CTkTextbox(self, width=680, height=400)
        textbox.pack(padx=10, pady=10, fill="both", expand=True)

        # ログを整形してテキストに書き込みます。
        for entry in entries:
            if entry.datetime_original:
                time_text = entry.datetime_original.strftime("%Y-%m-%d %H:%M:%S")
            else:
                time_text = "日時不明"

            gps_text = f"緯度: {entry.gps_latitude}, 経度: {entry.gps_longitude}"
            line = (
                f"{time_text} | {entry.filename} | {entry.status_text()} | {gps_text}\n"
            )
            textbox.insert("end", line)

        # 編集不可にします。
        textbox.configure(state="disabled")

        # 閉じるボタンを追加します。
        ctk.CTkButton(self, text="閉じる", command=self.destroy, width=120, height=35).pack(pady=(5, 15))


class MainApp(ctk.CTk):
    """
    メイン画面を構成するクラスです。
    """

    def __init__(self):
        super().__init__()
        self.title("GM Photo Tagger")
        self.geometry("1500x900")

        # 設定読み込み
        self.settings = SettingsManager.load()

        # UIを構築
        self.source_entry = None
        self.dest_entry = None
        self.gpx_entry = None
        self.email_entry = None
        self.password_entry = None
        self.settings_status_label = None
        self.tabview = None
        self.cache_dir_entry = None
        self.extensions_entry = None
        self.map_center_lat_entry = None
        self.map_center_lon_entry = None
        self.map_zoom_entry = None
        self.tile_server_url_entry = None
        self.tile_server_max_zoom_entry = None
        self.gpx_track_color_entry = None
        self.gpx_track_width_entry = None

        self.calendar = None
        self.range_label = None
        self.download_log_label = None
        self.start_date: Optional[date] = None
        self.end_date: Optional[date] = None
        self.selected_dates: Set[date] = set()  # スキャンで取得した撮影日を保存
        
        # 地図関連
        self.map_widget = None
        self.map_markers: List = []  # マーカーのリスト
        self.map_paths: List = []  # GPXパスのリスト
        self.photo_locations: List[dict] = []  # 写真の位置情報リスト
        self.downloaded_gpx_files: List[Path] = []  # ダウンロードされたGPXファイルリスト
        self.matching_gpx_files: List[Path] = []  # 撮影日時に該当するGPXファイルリスト

        self.setup_ui()

    def setup_ui(self) -> None:
        """
        メイン画面のUI部品を配置します。
        """
        # メインコンテナを左右分割
        main_container = ctk.CTkFrame(self, fg_color="transparent")
        main_container.pack(fill="both", expand=True)
        
        # 左側：タブビュー
        left_frame = ctk.CTkFrame(main_container, fg_color="transparent")
        left_frame.pack(side="left", fill="both", expand=True, padx=10, pady=10)
        
        # タブビューを作成
        self.tabview = ctk.CTkTabview(left_frame, width=600)
        self.tabview.pack(fill="both", expand=True)
        
        # メインタブ
        self.tabview.add("メイン")
        main_tab = self.tabview.tab("メイン")
        
        # 設定タブ
        self.tabview.add("設定")
        settings_tab = self.tabview.tab("設定")
        
        # 情報タブ
        self.tabview.add("情報")
        info_tab = self.tabview.tab("情報")
        
        # 右側フレーム（地図）
        self.right_frame = ctk.CTkFrame(main_container, fg_color="transparent")
        self.right_frame.pack(side="right", fill="both", expand=True, padx=10, pady=10)
        
        # メインタブのUI構築
        # 画像取り込み設定ラベル
        ctk.CTkLabel(
            main_tab,
            text="画像取り込み設定",
            font=("Yu Gothic UI", 20, "bold")
        ).pack(pady=(20, 10))
        
        self.source_entry = self.create_dir_selector(
            main_tab, "読み込む画像のディレクトリ", "img_src", "① 選択"
        )
        self.dest_entry = self.create_dir_selector(
            main_tab, "画像の取り込みディレクトリ", "img_dest", "② 選択"
        )
        self.gpx_entry = self.create_dir_selector(
            main_tab, "位置情報ファイルの保存先", "gpx_dir", "③ 選択"
        )

        self.setup_gpx_download_ui(main_tab)

        ctk.CTkButton(
            main_tab,
            text="⑥ 取り込み",
            fg_color="green",
            command=self.open_processing_popup,
            font=("Yu Gothic UI", 20),
        ).pack(pady=6)
        
        # 設定タブのUI構築
        self.setup_settings_tab(settings_tab)
        
        # 情報タブのUI構築
        self.setup_info_tab(info_tab)
        
        # 右側の地図UI構築
        self.setup_map_ui(self.right_frame)

    def setup_gpx_download_ui(self, parent_frame) -> None:
        """
        アクティビティダウンロード用のUIをメイン画面に配置します。
        """
        frame = ctk.CTkFrame(parent_frame)
        frame.pack(fill="x", padx=16, pady=10)

        ctk.CTkLabel(frame, text="アクティビティダウンロード", font=("Yu Gothic UI", 20, "bold")).pack(pady=(20, 4))

        # カレンダー用の固定サイズフレーム
        calendar_frame = ctk.CTkFrame(frame)
        calendar_frame.pack(pady=10, padx=10)
        
        self.calendar = Calendar(calendar_frame, selectmode="day", locale="ja_JP", font=("Yu Gothic UI", 20), weekendforeground="red", firstweekday="sunday", background="white", foreground="black", selectbackground="#2B7DE9")
        self.calendar.pack(pady=10)
        self.calendar.bind("<<CalendarSelected>>", self.on_date_selected)

        self.range_label = ctk.CTkLabel(frame, text="日付範囲: 未選択", font=("Yu Gothic UI", 18))
        self.range_label.pack(pady=4)

        # self.download_log_label = ctk.CTkLabel(frame, text="", font=("Yu Gothic UI", 12))
        # self.download_log_label.pack(pady=4)

        button_frame = ctk.CTkFrame(frame, fg_color="transparent")
        button_frame.pack(pady=(20, 30))

        ctk.CTkButton(
            button_frame, 
            text="④ 撮影日の自動取得", 
            command=self.scan_photo_dates, 
            font=("Yu Gothic UI", 18),
            fg_color="#2B7DE9"
        ).pack(side="left", padx=6)
        
        ctk.CTkButton(
            button_frame, 
            text="⑤ アクティビティをダウンロード", 
            command=self.start_download, 
            font=("Yu Gothic UI", 18),
            fg_color="#2B7DE9"
        ).pack(side="left", padx=6)

    def create_dir_selector(self, parent_frame, label_text: str, key: str, button_text: str = "選択") -> ctk.CTkEntry:
        """
        ディレクトリ選択のUIを作ります。
        """
        frame = ctk.CTkFrame(parent_frame, fg_color="transparent")
        frame.pack(fill="x", padx=16, pady=6)

        # ctk.CTkLabel(frame, text=label_text, font=("Yu Gothic UI", 18)).pack(side="left", padx=6)
        ctk.CTkLabel(frame, text=label_text, font=("Yu Gothic UI", 18), width=250, anchor="w").pack(side="left", padx=6)

        entry = ctk.CTkEntry(frame)
        entry.insert(0, self.settings.get(key, ""))
        entry.pack(side="left", padx=6, expand=True, fill="x")

        ctk.CTkButton(
            frame,
            text=button_text,
            command=lambda: self.select_dir(entry, key),
            font=("Yu Gothic UI", 18),
        ).pack(side="left", padx=6)

        return entry

    def select_dir(self, entry_widget: ctk.CTkEntry, key: str) -> None:
        """
        ディレクトリを選択して設定に保存します。
        """
        path = filedialog.askdirectory()
        if path:
            entry_widget.delete(0, "end")
            entry_widget.insert(0, path)
            SettingsManager.save({key: path})

    def setup_map_ui(self, parent_frame) -> None:
        """
        地図UIをセットアップします。
        パフォーマンス最適化を含みます。
        """
        # タイトル
        ctk.CTkLabel(parent_frame, text="撮影位置情報", font=("Yu Gothic UI", 18, "bold")).pack(pady=(10, 5))
        
        # キャッシュディレクトリを設定から取得
        cache_dir_str = self.settings.get("cache_dir", str(Path.home() / ".geotagphoto_cache"))
        cache_dir = Path(cache_dir_str)
        cache_dir.mkdir(exist_ok=True)
        
        # 地図ウィジェット（OpenStreetMap使用、キャッシュ有効化）
        self.map_widget = tkintermapview.TkinterMapView(
            parent_frame, 
            width=600, 
            height=600,
            database_path=str(cache_dir / "map_tiles.db")
        )
        self.map_widget.pack(fill="both", expand=True, padx=10, pady=10)
        
        # タイルサーバーを明示的に設定
        tile_server_url = self.settings.get("tile_server_url", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
        tile_server_max_zoom = int(self.settings.get("tile_server_max_zoom", "16"))
        self.map_widget.set_tile_server(tile_server_url, max_zoom=tile_server_max_zoom)
        
        # 初期位置を設定から取得
        default_lat = float(self.settings.get("map_center_lat", "35.6812"))
        default_lon = float(self.settings.get("map_center_lon", "139.7671"))
        default_zoom = int(self.settings.get("map_zoom", "13"))
        self.map_widget.set_position(default_lat, default_lon)
        self.map_widget.set_zoom(default_zoom)
        
        # 情報ラベル
        self.map_info_label = ctk.CTkLabel(
            parent_frame, 
            text="「取り込み」を実行すると、ジオタグが付与された写真の位置がマップに表示されます（テスト中）",
            font=("Yu Gothic UI", 12),
            text_color="gray"
        )
        self.map_info_label.pack(pady=5)
        
        # マップ更新ボタン
        refresh_button_frame = ctk.CTkFrame(parent_frame, fg_color="transparent")
        refresh_button_frame.pack(pady=5)
        
        ctk.CTkButton(
            refresh_button_frame,
            text="🔄 マップのリロード",
            command=self.refresh_map,
            font=("Yu Gothic UI", 14),
            width=170,
            height=35,
            fg_color="#4A90E2",
            hover_color="#357ABD"
        ).pack(side="left", padx=5)

    def scan_photo_dates(self) -> None:
        """
        画像ディレクトリ内の画像ファイルをスキャンし、撮影日を取得してカレンダーに表示します。
        """
        source_dir = self.source_entry.get()
        if not source_dir or not os.path.exists(source_dir):
            messagebox.showwarning("確認", "読み込む画像のディレクトリが設定されていないか、存在しません。")
            return
        
        # 進捗表示用のポップアップを表示
        self.show_scanning_progress(Path(source_dir))
    
    def show_scanning_progress(self, source_dir: Path) -> None:
        """
        スキャン処理を別スレッドで実行し、進捗を表示します。
        """
        progress_window = ctk.CTkToplevel(self)
        progress_window.title("スキャン中...")
        progress_window.geometry("600x520")
        progress_window.transient(self)
        # 下のウィンドウの操作をロックします。
        progress_window.grab_set()
        
        # コンテンツフレームを作成（後で削除して再構築するため）
        content_frame = ctk.CTkFrame(progress_window, fg_color="transparent")
        content_frame.pack(fill="both", expand=True, padx=20, pady=20)
        
        title_label = ctk.CTkLabel(content_frame, text="画像の撮影日をスキャン中...", font=("Yu Gothic UI", 16, "bold"))
        title_label.pack(pady=10)
        
        progress_label = ctk.CTkLabel(content_frame, text="0 ファイル処理済み", font=("Yu Gothic UI", 12))
        progress_label.pack(pady=10)
        
        def scan_thread():
            image_extensions = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic"}
            found_dates = set()
            file_count = 0
            
            for file_path in source_dir.rglob("*"):
                if file_path.is_file() and file_path.suffix.lower() in image_extensions:
                    file_count += 1
                    try:
                        img = Image.open(file_path)
                        exif_data = img.getexif()
                        
                        if exif_data:
                            # DateTimeOriginal (タグ36867) または DateTime (タグ306) を取得
                            date_str = exif_data.get(36867) or exif_data.get(306)
                            if date_str:
                                # "YYYY:MM:DD HH:MM:SS" 形式をパース
                                photo_datetime = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
                                found_dates.add(photo_datetime.date())
                    except Exception:
                        # EXIF情報がない、または読み込めないファイルはスキップ
                        pass
                    
                    # 進捗更新
                    if file_count % 10 == 0:
                        progress_window.after(0, lambda c=file_count: progress_label.configure(text=f"{c} ファイル処理済み"))
            
            # 結果を反映
            def update_result():
                # コンテンツフレームを削除して再構築
                content_frame.destroy()
                
                result_frame = ctk.CTkFrame(progress_window, fg_color="transparent")
                result_frame.pack(fill="both", expand=True, padx=20, pady=20)
                
                progress_window.title("スキャン完了")
                
                if found_dates:
                    self.selected_dates = found_dates
                    self.start_date = None
                    self.end_date = None
                    self.highlight_date_range()
                    self.update_range_label()
                    
                    # 完了メッセージ
                    ctk.CTkLabel(
                        result_frame,
                        text="スキャン完了",
                        font=("Yu Gothic UI", 18, "bold"),
                        text_color="#2B7DE9"
                    ).pack(pady=10)
                    
                    ctk.CTkLabel(
                        result_frame,
                        text=f"{file_count} 個のファイルをスキャンしました",
                        font=("Yu Gothic UI", 14)
                    ).pack(pady=5)
                    
                    ctk.CTkLabel(
                        result_frame,
                        text=f"{len(found_dates)} 個のユニークな撮影日が見つかりました",
                        font=("Yu Gothic UI", 14)
                    ).pack(pady=5)
                    
                    # 検出された日付リスト
                    ctk.CTkLabel(
                        result_frame,
                        text="検出された撮影日:",
                        font=("Yu Gothic UI", 12, "bold")
                    ).pack(pady=(15, 5))
                    
                    # スクロール可能なテキストボックスで日付を表示
                    dates_text = ctk.CTkTextbox(result_frame, height=150, width=450)
                    dates_text.pack(pady=5)
                    
                    sorted_dates = sorted(found_dates)
                    for d in sorted_dates:
                        dates_text.insert("end", f"  • {d.year}年{d.month}月{d.day}日\n")
                    dates_text.configure(state="disabled")
                else:
                    # 日付が見つからなかった場合
                    ctk.CTkLabel(
                        result_frame,
                        text="結果",
                        font=("Yu Gothic UI", 18, "bold")
                    ).pack(pady=10)
                    
                    ctk.CTkLabel(
                        result_frame,
                        text=f"{file_count} 個のファイルをスキャンしましたが、\n撮影日情報が見つかりませんでした。",
                        font=("Yu Gothic UI", 14)
                    ).pack(pady=20)
                
                # 完了メッセージ（次のステップを案内します）
                ctk.CTkLabel(
                    result_frame,
                    text="✅ このポップアップを閉じてから、GPXのダウンロードを行ってください。",
                    font=("Yu Gothic UI", 13, "bold"),
                    text_color="#2FA84F",
                    wraplength=520,
                    justify="center"
                ).pack(pady=(10, 5))

                # 閉じるボタン
                ctk.CTkButton(
                    result_frame,
                    text="閉じる",
                    command=progress_window.destroy,
                    font=("Yu Gothic UI", 14),
                    width=120
                ).pack(pady=(0, 20))
            
            progress_window.after(0, update_result)
        
        threading.Thread(target=scan_thread, daemon=True).start()
    
    def highlight_date_range(self) -> None:
        """
        選択された日付範囲またはスキャンで取得した日付をカレンダー上でハイライト表示します。
        """
        # 既存のイベントをすべてクリア
        for event_id in self.calendar.get_calevents():
            self.calendar.calevent_remove(event_id)
        
        # スキャンで取得した日付がある場合、それを優先表示
        if self.selected_dates:
            for target_date in self.selected_dates:
                self.calendar.calevent_create(target_date, "", "scanned")
            self.calendar.tag_config("scanned", background="#FFD700", foreground="black")
        # 日付範囲がある場合、各日付にイベントを作成
        elif self.start_date and self.end_date:
            current = self.start_date
            while current <= self.end_date:
                self.calendar.calevent_create(current, "", "selected")
                current += timedelta(days=1)
            # タグに色を設定
            self.calendar.tag_config("selected", background="lightgreen", foreground="black")
        elif self.start_date:
            # 開始日のみの場合
            self.calendar.calevent_create(self.start_date, "", "selected")
            self.calendar.tag_config("selected", background="lightgreen", foreground="black")
    
    def refresh_map(self) -> None:
        """
        マップを手動で更新します（マーカーとGPX軌跡を再描画）。
        """
        if not self.map_widget:
            messagebox.showinfo("情報", "マップが初期化されていません。")
            return
        
        # マーカーを更新
        self.update_map_markers()
        
        # GPX軌跡を更新（matching_gpx_filesがあればそれを使用、なければ最新5件）
        if self.matching_gpx_files:
            self.update_gpx_paths(self.matching_gpx_files)
        else:
            self.update_gpx_paths()
        
        messagebox.showinfo("完了", "マップを更新しました。")

    def update_map_markers(self) -> None:
        """
        地図上のマーカーを更新します。
        """
        if not self.map_widget:
            return
        
        # 既存のマーカーをすべて削除
        for marker in self.map_markers:
            marker.delete()
        self.map_markers.clear()
        
        if not self.photo_locations:
            self.map_info_label.configure(
                text="GPS情報を持つ写真が見つかりませんでした",
                text_color="orange"
            )
            return
        
        # マーカーを追加
        marker_count = len(self.photo_locations)
        enable_click = marker_count <= 500  # 500件以下の場合のみクリックイベントを有効化
        
        for idx, location in enumerate(self.photo_locations):
            marker = self.map_widget.set_marker(
                location["lat"],
                location["lon"],
                text=""  # テキストなし（パフォーマンス向上）
            )
            # 500件以下の場合のみクリックイベントを設定
            if enable_click:
                # クロージャの問題を回避するため、デフォルト引数で値を固定
                marker.command = lambda m=marker, loc=location: self.show_photo_thumbnail(loc)
            self.map_markers.append(marker)
        
        # ジオタグ付き写真のうち最も古い撮影時刻の位置をマップ中心座標として設定
        if self.photo_locations:
            # date が None でないものだけを対象に最小（最古）を探します。
            dated = [loc for loc in self.photo_locations if loc.get("date") is not None]
            if dated:
                oldest = min(dated, key=lambda loc: loc["date"])
            else:
                oldest = self.photo_locations[0]
            self.map_widget.set_position(oldest["lat"], oldest["lon"])
            self.map_widget.set_zoom(12)
        
        # 情報ラベル更新
        if enable_click:
            self.map_info_label.configure(
                text=f"{marker_count} 個の写真の位置を表示しています（マーカーをクリックしてサムネイルを表示（テスト中））",
                text_color="#2FA84F"
            )
        else:
            self.map_info_label.configure(
                text=f"{marker_count} 個の写真の位置を表示しています（マーカー数が多いためクリック無効）",
                text_color="#E6A817"
            )
        
        # GPX軌跡を描画
        self.update_gpx_paths()
    
    def update_gpx_paths(self, gpx_files: Optional[List[Path]] = None) -> None:
        """
        地図上にGPX軌跡を描画します。
        
        Args:
            gpx_files: 描画するGPXファイルのリスト。Noneの場合は全GPXファイルから最新5件を使用。
        """
        if not self.map_widget:
            print("ℹ️ GPX軌跡: マップウィジェットが存在しません")
            return
        
        # 既存のパスをすべて削除
        for path in self.map_paths:
            path.delete()
        self.map_paths.clear()
        
        # 設定でGPX軌跡表示が無効の場合は何もしない
        if not self.settings.get("show_gpx_path", True):
            print("ℹ️ GPX軌跡: 設定で無効化されています")
            return
        
        # GPXファイルリストが指定されていない場合は、ディレクトリから取得
        if gpx_files is None:
            # GPXディレクトリから最新のGPXファイルを取得
            gpx_dir_str = self.settings.get("gpx_dir", "")
            if not gpx_dir_str:
                print("⚠️ GPX軌跡: GPXディレクトリが設定されていません")
                return
            
            gpx_dir = Path(gpx_dir_str)
            if not gpx_dir.exists():
                print(f"⚠️ GPX軌跡: ディレクトリが存在しません: {gpx_dir}")
                return
                
            all_gpx_files = list_gpx_or_tcx_files(gpx_dir)
            
            if not all_gpx_files:
                print(f"⚠️ GPX軌跡: GPXファイルが見つかりませんでした: {gpx_dir}")
                return
            
            gpx_files = all_gpx_files[:5]  # 最新5件
            print(f"✓ GPX軌跡: {len(all_gpx_files)}件のGPXファイルを検出（最新5件を表示）")
        else:
            print(f"✓ GPX軌跡: 指定された{len(gpx_files)}件のGPXファイルを描画")
        
        # 色と幅を設定から取得
        track_color = self.settings.get("gpx_track_color", "#FF0000")
        track_width = int(self.settings.get("gpx_track_width", "3"))
        
        # GPXファイルの軌跡を描画
        for gpx_file in gpx_files:
            coordinates = parse_gpx_track(gpx_file)
            
            if len(coordinates) >= 2:  # 最低2点必要
                try:
                    path = self.map_widget.set_path(
                        coordinates,
                        color=track_color,
                        width=track_width
                    )
                    self.map_paths.append(path)
                    print(f"  ✓ {gpx_file.name}: {len(coordinates)}点の軌跡を描画")
                except Exception as e:
                    print(f"  ✘ GPXパス描画エラー ({gpx_file.name}): {e}")
            else:
                print(f"  ⚠️ {gpx_file.name}: 座標が不十分です ({len(coordinates)}点)")
    
    def add_single_marker(self, location: dict) -> None:
        """
        地図に単一のマーカーを追加します（リアルタイム更新用）。
        """
        if not self.map_widget:
            return
        
        marker = self.map_widget.set_marker(
            location["lat"],
            location["lon"],
            text=""  # テキストなし（パフォーマンス向上）
        )
        # 500件以下の場合のみクリックイベントを設定
        if len(self.photo_locations) <= 500:
            # クロージャの問題を回避するため、デフォルト引数で値を固定
            marker.command = lambda m=marker, loc=location: self.show_photo_thumbnail(loc)
        self.map_markers.append(marker)
        
        # 最初のマーカーの場合は位置を設定
        if len(self.map_markers) == 1:
            self.map_widget.set_position(location["lat"], location["lon"])
            self.map_widget.set_zoom(12)
        
        # 情報ラベル更新
        marker_count = len(self.photo_locations)
        if marker_count <= 500:
            self.map_info_label.configure(
                text=f"{marker_count} 個の写真の位置を表示しています（マーカーをクリックしてサムネイルを表示）",
                text_color="#2FA84F"
            )
        else:
            self.map_info_label.configure(
                text=f"{marker_count} 個の写真の位置を表示しています（マーカー数が多いためクリック無効）",
                text_color="#E6A817"
            )

    def extract_thumbnail_with_exiftool(self, file_path: str) -> Optional[Image.Image]:
        """
        ExifToolを使用して画像ファイルから埋め込みサムネイルを抽出します。
        RAWファイルなどPILで直接開けないファイルに対応します。
        軽量化のためThumbnailImageを優先的に使用します。
        """
        exiftool_path = find_exiftool()
        
        # ThumbnailImageを優先（軽量・高速）、なければPreviewImageを試す
        for tag in ["-ThumbnailImage", "-PreviewImage"]:
            try:
                command = [exiftool_path, "-b", tag, file_path]
                result = subprocess.run(command, capture_output=True, check=False, timeout=5)
                
                if result.returncode == 0 and result.stdout:
                    # バイナリデータをPIL Imageに変換
                    img = Image.open(io.BytesIO(result.stdout))
                    return img
            except Exception:
                continue
        
        return None

    def show_photo_thumbnail(self, location: dict) -> None:
        """
        写真のサムネイルをポップアップで表示します。
        RAWファイル（.PEF, .CR2, .NEF等）にも対応します。
        画像読み込みを非同期化し、レスポンスを向上させています。
        """
        popup = ctk.CTkToplevel(self)
        popup.title("写真プレビュー")
        popup.geometry("350x450")
        popup.transient(self)
        popup.resizable(False, False)
        
        # ファイル名
        ctk.CTkLabel(popup, text=location["file_name"], font=("Yu Gothic UI", 14, "bold")).pack(pady=10)
        
        # 撮影日時
        if location.get("date"):
            date_text = location["date"].strftime("%Y年%m月%d日 %H:%M:%S")
            ctk.CTkLabel(popup, text=f"撮影日時: {date_text}", font=("Yu Gothic UI", 12)).pack(pady=5)
        
        # GPS座標
        ctk.CTkLabel(
            popup,
            text=f"緯度: {location['lat']:.6f}, 経度: {location['lon']:.6f}",
            font=("Yu Gothic UI", 12)
        ).pack(pady=5)
        
        # 画像プレースホルダー（読み込み中表示）
        loading_label = ctk.CTkLabel(
            popup, 
            text="🔄 読み込み中...", 
            font=("Yu Gothic UI", 14),
            text_color="gray"
        )
        loading_label.pack(pady=50)
        
        # 画像コンテナフレーム（後で画像表示用）
        image_frame = ctk.CTkFrame(popup, fg_color="transparent")
        image_frame.pack(pady=10)
        
        # 閉じるボタン
        close_button = ctk.CTkButton(popup, text="閉じる", command=popup.destroy, font=("Yu Gothic UI", 12))
        close_button.pack(pady=10)
        
        # 画像を非同期で読み込み
        def load_image_async():
            img = None
            error_msg = None
            
            try:
                # まずPILで直接開くことを試みる（JPEG、PNG等）
                img = Image.open(location["file_path"])
            except Exception as e:
                # PILで開けない場合、ExifToolでサムネイルを抽出（RAWファイル等）
                try:
                    img = self.extract_thumbnail_with_exiftool(location["file_path"])
                    if img is None:
                        error_msg = f"サムネイルが見つかりません\nファイル: {location['file_name']}"
                except Exception as e2:
                    error_msg = f"画像の読み込みに失敗しました\nファイル: {location['file_name']}\nエラー: {str(e2)}"
            
            # メインスレッドで画像を表示
            def display_image():
                try:
                    # ポップアップがまだ存在するか確認
                    if not popup.winfo_exists():
                        return
                    
                    # 読み込み中ラベルを削除
                    if loading_label.winfo_exists():
                        loading_label.destroy()
                    
                    if img:
                        try:
                            # サムネイルサイズに縮小（小さめのサイズで高速化）
                            img.thumbnail((300, 300))
                            # PIL ImageをPhotoImageに変換
                            photo = ImageTk.PhotoImage(img)
                            
                            # Tkinter LabelでPhotoImageを表示
                            label = tk.Label(image_frame, image=photo)
                            label.image = photo  # 参照を保持
                            label.pack()
                        except Exception as e:
                            ctk.CTkLabel(
                                image_frame, 
                                text=f"画像の表示に失敗しました: {e}", 
                                font=("Yu Gothic UI", 12), 
                                text_color="red"
                            ).pack()
                    elif error_msg:
                        ctk.CTkLabel(
                            image_frame, 
                            text=error_msg, 
                            font=("Yu Gothic UI", 10), 
                            text_color="orange",
                            wraplength=320
                        ).pack()
                except Exception as e:
                    # display_image内でのエラーをキャッチ
                    print(f"display_image error: {e}")
            
            # メインスレッドで画像表示を実行
            try:
                if popup.winfo_exists():
                    popup.after(0, display_image)
            except Exception as e:
                print(f"popup.after error: {e}")
        
        # 別スレッドで画像読み込みを開始
        thread = threading.Thread(target=load_image_async, daemon=True)
        thread.start()

    def on_date_selected(self, _event) -> None:
        """
        カレンダーがクリックされたときに日付範囲を更新します。

        1回目のクリック: 開始日を設定
        2回目のクリック: 終了日を設定
        3回目のクリック: 新たな開始日としてやり直し
        
        ※手動選択時はスキャン結果をクリアします
        """
        selected_date = self.calendar.selection_get()
        
        # スキャン結果をクリア（手動選択モードに切り替え）
        self.selected_dates.clear()

        if self.start_date is None or (self.start_date and self.end_date):
            self.start_date = selected_date
            self.end_date = None
        else:
            if selected_date < self.start_date:
                self.end_date = self.start_date
                self.start_date = selected_date
            else:
                self.end_date = selected_date

        self.update_range_label()
        self.highlight_date_range()

    def update_range_label(self) -> None:
        """
        日付範囲をラベルに表示します。
        """
        if self.selected_dates:
            sorted_dates = sorted(self.selected_dates)
            # m月d日形式で表示（3日まで）
            display_dates = [f"{d.month}月{d.day}日" for d in sorted_dates[:3]]
            dates_str = "、".join(display_dates)
            if len(sorted_dates) > 3:
                text = f"選択済み: {dates_str}..."
            else:
                text = f"選択済み: {dates_str}"
        elif self.start_date and self.end_date:
            text = f"日付範囲: {self.start_date} ～ {self.end_date}"
        elif self.start_date:
            text = f"開始日: {self.start_date}"
        else:
            text = "日付範囲: 未選択"
        self.range_label.configure(text=text)

    def open_processing_popup(self) -> None:
        """
        取り込み処理用のポップアップを開きます。
        """
        ProcessingPopup(self, self.source_entry, self.dest_entry, self.gpx_entry)

    def setup_settings_tab(self, parent_frame) -> None:
        """
        設定タブのUIを構築します。
        """
        # スクロール可能なフレームを作成
        scrollable_frame = ctk.CTkScrollableFrame(parent_frame, fg_color="transparent")
        scrollable_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # ラベルの幅を統一
        LABEL_WIDTH = 150

        # 設定保存ボタンの案内
        ctk.CTkLabel(
            scrollable_frame,
            text="※ 設定を変更したら、ページ下部の「設定を保存」ボタンを押してください",
            font=("Yu Gothic UI", 12),
            text_color="#E8A030"
        ).pack(pady=(10, 0))

        # === キャッシュディレクトリ設定 ===
        ctk.CTkLabel(
            scrollable_frame, 
            text="キャッシュ・設定保存先", 
            font=("Yu Gothic UI", 20, "bold")
        ).pack(pady=(30, 10))
        
        ctk.CTkLabel(
            scrollable_frame,
            text="マップタイルキャッシュと設定ファイルの保存先を指定します",
            font=("Yu Gothic UI", 12),
            text_color="gray"
        ).pack(pady=(0, 10))
        
        cache_frame = ctk.CTkFrame(scrollable_frame, fg_color="transparent")
        cache_frame.pack(pady=5, padx=40, fill="x")
        ctk.CTkLabel(cache_frame, text="保存先:", font=("Yu Gothic UI", 14), width=LABEL_WIDTH, anchor="w").pack(side="left", padx=5)
        self.cache_dir_entry = ctk.CTkEntry(cache_frame, font=("Yu Gothic UI", 14))
        default_cache = self.settings.get("cache_dir", str(SettingsManager._get_default_dir()))
        self.cache_dir_entry.insert(0, default_cache)
        self.cache_dir_entry.pack(side="left", padx=5, fill="x", expand=True)
        ctk.CTkButton(
            cache_frame,
            text="選択",
            command=self.select_cache_dir,
            font=("Yu Gothic UI", 14),
            width=80
        ).pack(side="left", padx=5)
        
        # === Garmin Connect設定 ===
        ctk.CTkLabel(
            scrollable_frame, 
            text="Garmin Connect 設定", 
            font=("Yu Gothic UI", 20, "bold")
        ).pack(pady=(20, 10))
        
        # 説明
        ctk.CTkLabel(
            scrollable_frame,
            text="Garmin Connectのアクティビティをダウンロードするための認証情報と形式を設定します",
            font=("Yu Gothic UI", 12),
            text_color="gray"
        ).pack(pady=(0, 20))
        
        # ユーザー名
        email_frame = ctk.CTkFrame(scrollable_frame, fg_color="transparent")
        email_frame.pack(pady=5, padx=40, fill="x")
        ctk.CTkLabel(email_frame, text="ユーザー名 (Email):", font=("Yu Gothic UI", 14), width=LABEL_WIDTH, anchor="w").pack(side="left", padx=5)
        self.email_entry = ctk.CTkEntry(email_frame, font=("Yu Gothic UI", 14))
        self.email_entry.insert(0, self.settings.get("email", ""))
        self.email_entry.pack(side="left", padx=5, fill="x", expand=True)
        
        # パスワード
        password_frame = ctk.CTkFrame(scrollable_frame, fg_color="transparent")
        password_frame.pack(pady=5, padx=40, fill="x")
        ctk.CTkLabel(password_frame, text="パスワード:", font=("Yu Gothic UI", 14), width=LABEL_WIDTH, anchor="w").pack(side="left", padx=5)
        self.password_entry = ctk.CTkEntry(password_frame, font=("Yu Gothic UI", 14), show="●")
        self.password_entry.insert(0, decode_password(self.settings.get("password_encoded", "")))
        self.password_entry.pack(side="left", padx=5, fill="x", expand=True)
        
        # ダウンロード形式
        dl_fmt_frame = ctk.CTkFrame(scrollable_frame, fg_color="transparent")
        dl_fmt_frame.pack(pady=5, padx=40, fill="x")
        ctk.CTkLabel(dl_fmt_frame, text="ダウンロード形式:", font=("Yu Gothic UI", 14), width=LABEL_WIDTH, anchor="w").pack(side="left", padx=5)
        # 現在の設定値を初期値として設定（デフォルト: gpx）
        current_dl_fmt = self.settings.get("activity_download_format", "gpx")
        self.dl_format_var = ctk.StringVar(value=current_dl_fmt)
        ctk.CTkRadioButton(
            dl_fmt_frame,
            text="GPX（軌跡のみ、推奨）",
            variable=self.dl_format_var,
            value="gpx",
            font=("Yu Gothic UI", 14)
        ).pack(side="left", padx=10)
        ctk.CTkRadioButton(
            dl_fmt_frame,
            text="TCX（心拍数・ケイデンス等も含む）",
            variable=self.dl_format_var,
            value="tcx",
            font=("Yu Gothic UI", 14)
        ).pack(side="left", padx=10)
        
        # === RAW拡張子設定 ===
        ctk.CTkLabel(
            scrollable_frame, 
            text="対応ファイル拡張子", 
            font=("Yu Gothic UI", 20, "bold")
        ).pack(pady=(30, 10))
        
        ctk.CTkLabel(
            scrollable_frame,
            text="RAWファイルなどの追加拡張子を英数小文字、カンマ区切りで指定します（例: .cr2,.nef,.arw）",
            font=("Yu Gothic UI", 12),
            text_color="gray"
        ).pack(pady=(0, 10))
        
        extensions_frame = ctk.CTkFrame(scrollable_frame, fg_color="transparent")
        extensions_frame.pack(pady=5, padx=40, fill="x")
        ctk.CTkLabel(extensions_frame, text="カスタム拡張子:", font=("Yu Gothic UI", 14), width=LABEL_WIDTH, anchor="w").pack(side="left", padx=5)
        self.extensions_entry = ctk.CTkEntry(extensions_frame, font=("Yu Gothic UI", 14))
        custom_exts = self.settings.get("custom_extensions", ".jpg,.jpeg,.png,.tif,.tiff,.heic,.cr2,.cr3,.nef,.nrw,.arw,.raf,.orf,.rw2,.pef,.dng,.rwl,.mov,.mp4,.avi,.mts,.m2ts")
        self.extensions_entry.insert(0, custom_exts)
        self.extensions_entry.pack(side="left", padx=5, fill="x", expand=True)
        
        # === ジオタグ上書き設定 ===
        ctk.CTkLabel(
            scrollable_frame, 
            text="ジオタグ上書き設定", 
            font=("Yu Gothic UI", 20, "bold")
        ).pack(pady=(30, 10))
        
        ctk.CTkLabel(
            scrollable_frame,
            text="取り込み元の写真に既にジオタグが付与されている場合の処理を設定します",
            font=("Yu Gothic UI", 12),
            text_color="gray"
        ).pack(pady=(0, 10))
        
        overwrite_geotag_frame = ctk.CTkFrame(scrollable_frame, fg_color="transparent")
        overwrite_geotag_frame.pack(pady=5, padx=40, fill="x")
        
        self.overwrite_existing_geotag_var = ctk.BooleanVar(value=self.settings.get("overwrite_existing_geotag", False))
        self.overwrite_existing_geotag_checkbox = ctk.CTkCheckBox(
            overwrite_geotag_frame,
            text="既存のジオタグを上書きする",
            variable=self.overwrite_existing_geotag_var,
            font=("Yu Gothic UI", 14)
        )
        self.overwrite_existing_geotag_checkbox.pack(side="left", padx=5)
        
        ctk.CTkLabel(
            overwrite_geotag_frame,
            text="※ チェックしない場合、既にGPS情報がある写真はスキップされます",
            font=("Yu Gothic UI", 11),
            text_color="gray"
        ).pack(side="left", padx=10)
        
        # === ExifTool並列処理設定 ===
        ctk.CTkLabel(
            scrollable_frame, 
            text="ExifTool並列処理設定", 
            font=("Yu Gothic UI", 20, "bold")
        ).pack(pady=(30, 10))
        
        ctk.CTkLabel(
            scrollable_frame,
            text="ジオタグ付与処理を高速化するための並列ワーカー数を設定します（大量のファイルを処理する際に効果があります）",
            font=("Yu Gothic UI", 12),
            text_color="gray"
        ).pack(pady=(0, 10))
        
        exiftool_workers_frame = ctk.CTkFrame(scrollable_frame, fg_color="transparent")
        exiftool_workers_frame.pack(pady=5, padx=40, fill="x")
        ctk.CTkLabel(exiftool_workers_frame, text="並列ワーカー数 (1-16):", font=("Yu Gothic UI", 14), width=LABEL_WIDTH, anchor="w").pack(side="left", padx=5)
        self.exiftool_max_workers_entry = ctk.CTkEntry(exiftool_workers_frame, font=("Yu Gothic UI", 14), width=200)
        default_workers = self.settings.get("exiftool_max_workers", "4")
        self.exiftool_max_workers_entry.insert(0, str(default_workers))
        self.exiftool_max_workers_entry.pack(side="left", padx=5)
        ctk.CTkLabel(
            exiftool_workers_frame, 
            text="※ 推奨: 4（多すぎるとディスク負荷が増加します）", 
            font=("Yu Gothic UI", 11), 
            text_color="gray"
        ).pack(side="left", padx=5)
        
        # === 地図中心座標設定 ===
        ctk.CTkLabel(
            scrollable_frame, 
            text="マップ初期表示設定", 
            font=("Yu Gothic UI", 20, "bold")
        ).pack(pady=(30, 10))
        
        ctk.CTkLabel(
            scrollable_frame,
            text="起動時に表示する地図の中心座標を設定します（緯度,経度 形式）",
            font=("Yu Gothic UI", 12),
            text_color="gray"
        ).pack(pady=(0, 10))
        
        # 緯度・経度（カンマ区切り）
        coord_frame = ctk.CTkFrame(scrollable_frame, fg_color="transparent")
        coord_frame.pack(pady=5, padx=40, fill="x")
        
        ctk.CTkLabel(coord_frame, text="中心座標 (緯度,経度):", font=("Yu Gothic UI", 14), width=LABEL_WIDTH, anchor="w").pack(side="left", padx=5)
        self.map_center_coord_entry = ctk.CTkEntry(coord_frame, font=("Yu Gothic UI", 14), width=200)
        default_lat = self.settings.get("map_center_lat", "35.6812")
        default_lon = self.settings.get("map_center_lon", "139.7671")
        self.map_center_coord_entry.insert(0, f"{default_lat},{default_lon}")
        self.map_center_coord_entry.pack(side="left", padx=5)
        ctk.CTkLabel(coord_frame, text="例: 35.6812,139.7671", font=("Yu Gothic UI", 11), text_color="gray").pack(side="left", padx=5)
        
        # ズーム倍率
        zoom_frame = ctk.CTkFrame(scrollable_frame, fg_color="transparent")
        zoom_frame.pack(pady=5, padx=40, fill="x")
        ctk.CTkLabel(zoom_frame, text="ズーム倍率 (1-19):", font=("Yu Gothic UI", 14), width=LABEL_WIDTH, anchor="w").pack(side="left", padx=5)
        self.map_zoom_entry = ctk.CTkEntry(zoom_frame, font=("Yu Gothic UI", 14), width=200)
        default_zoom = self.settings.get("map_zoom", "10")
        self.map_zoom_entry.insert(0, str(default_zoom))
        self.map_zoom_entry.pack(side="left", padx=5)
        ctk.CTkLabel(zoom_frame, text="(デフォルト: 10)", font=("Yu Gothic UI", 11), text_color="gray").pack(side="left", padx=5)
        
        # === GPX軌跡表示設定 ===
        ctk.CTkLabel(
            scrollable_frame, 
            text="GPX軌跡表示設定", 
            font=("Yu Gothic UI", 20, "bold")
        ).pack(pady=(30, 10))
        
        ctk.CTkLabel(
            scrollable_frame,
            text="GPXファイルの移動軌跡をマップ上に表示します",
            font=("Yu Gothic UI", 12),
            text_color="gray"
        ).pack(pady=(0, 10))
        
        gpx_display_frame = ctk.CTkFrame(scrollable_frame, fg_color="transparent")
        gpx_display_frame.pack(pady=5, padx=40, fill="x")
        
        self.show_gpx_path_var = ctk.BooleanVar(value=self.settings.get("show_gpx_path", True))
        self.show_gpx_path_checkbox = ctk.CTkCheckBox(
            gpx_display_frame,
            text="GPX軌跡をマップに表示する",
            variable=self.show_gpx_path_var,
            font=("Yu Gothic UI", 14)
        )
        self.show_gpx_path_checkbox.pack(side="left", padx=5)
        
        # GPX軌跡の色設定
        gpx_color_frame = ctk.CTkFrame(scrollable_frame, fg_color="transparent")
        gpx_color_frame.pack(pady=5, padx=40, fill="x")
        ctk.CTkLabel(gpx_color_frame, text="軌跡の色 (16進数):", font=("Yu Gothic UI", 14), width=LABEL_WIDTH, anchor="w").pack(side="left", padx=5)
        self.gpx_track_color_entry = ctk.CTkEntry(gpx_color_frame, font=("Yu Gothic UI", 14), width=200)
        default_track_color = self.settings.get("gpx_track_color", "#FF0000")
        self.gpx_track_color_entry.insert(0, default_track_color)
        self.gpx_track_color_entry.pack(side="left", padx=5)
        ctk.CTkLabel(gpx_color_frame, text="例: #FF0000 (赤)", font=("Yu Gothic UI", 11), text_color="gray").pack(side="left", padx=5)
        
        # GPX軌跡の幅設定
        gpx_width_frame = ctk.CTkFrame(scrollable_frame, fg_color="transparent")
        gpx_width_frame.pack(pady=5, padx=40, fill="x")
        ctk.CTkLabel(gpx_width_frame, text="軌跡の幅 (1-10):", font=("Yu Gothic UI", 14), width=LABEL_WIDTH, anchor="w").pack(side="left", padx=5)
        self.gpx_track_width_entry = ctk.CTkEntry(gpx_width_frame, font=("Yu Gothic UI", 14), width=200)
        default_track_width = self.settings.get("gpx_track_width", "3")
        self.gpx_track_width_entry.insert(0, str(default_track_width))
        self.gpx_track_width_entry.pack(side="left", padx=5)
        ctk.CTkLabel(gpx_width_frame, text="(デフォルト: 3)", font=("Yu Gothic UI", 11), text_color="gray").pack(side="left", padx=5)
        
        # === タイルサーバー設定 ===
        ctk.CTkLabel(
            scrollable_frame, 
            text="タイルサーバー設定", 
            font=("Yu Gothic UI", 20, "bold")
        ).pack(pady=(30, 10))
        
        ctk.CTkLabel(
            scrollable_frame,
            text="マップタイルの取得先URLと最大ズームレベルを設定します",
            font=("Yu Gothic UI", 12),
            text_color="gray"
        ).pack(pady=(0, 10))
        
        # タイルサーバーURL
        tile_url_frame = ctk.CTkFrame(scrollable_frame, fg_color="transparent")
        tile_url_frame.pack(pady=5, padx=40, fill="x")
        ctk.CTkLabel(tile_url_frame, text="タイルサーバーURL:", font=("Yu Gothic UI", 14), width=LABEL_WIDTH, anchor="w").pack(side="left", padx=5)
        self.tile_server_url_entry = ctk.CTkEntry(tile_url_frame, font=("Yu Gothic UI", 14))
        default_tile_url = self.settings.get("tile_server_url", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
        self.tile_server_url_entry.insert(0, default_tile_url)
        self.tile_server_url_entry.pack(side="left", padx=5, fill="x", expand=True)
        
        # 最大ズーム
        tile_zoom_frame = ctk.CTkFrame(scrollable_frame, fg_color="transparent")
        tile_zoom_frame.pack(pady=5, padx=40, fill="x")
        ctk.CTkLabel(tile_zoom_frame, text="最大ズーム (1-22):", font=("Yu Gothic UI", 14), width=LABEL_WIDTH, anchor="w").pack(side="left", padx=5)
        self.tile_server_max_zoom_entry = ctk.CTkEntry(tile_zoom_frame, font=("Yu Gothic UI", 14), width=200)
        default_max_zoom = self.settings.get("tile_server_max_zoom", "16")
        self.tile_server_max_zoom_entry.insert(0, str(default_max_zoom))
        self.tile_server_max_zoom_entry.pack(side="left", padx=5)
        ctk.CTkLabel(tile_zoom_frame, text="(デフォルト: 16)", font=("Yu Gothic UI", 11), text_color="gray").pack(side="left", padx=5)
        
        # === カメラのタイムゾーン設定 ===
        ctk.CTkLabel(
            scrollable_frame,
            text="カメラのタイムゾーン設定",
            font=("Yu Gothic UI", 20, "bold")
        ).pack(pady=(30, 10))
        
        ctk.CTkLabel(
            scrollable_frame,
            text="カメラに設定されているタイムゾーンを指定します。写真のEXIF日時にタイムゾーン情報が含まれない場合、ここで設定したタイムゾーンで処理されます",
            font=("Yu Gothic UI", 12),
            text_color="gray",
            wraplength=700,
            justify="left"
        ).pack(pady=(0, 10))
        
        # 選択肢となるタイムゾーン一覧（IANA形式）
        # よく使われるタイムゾーンをリストアップしています。
        TIMEZONE_OPTIONS = [
            "Asia/Tokyo",        # 日本 UTC+9
            "Asia/Seoul",        # 韓国 UTC+9
            "Asia/Shanghai",     # 中国 UTC+8
            "Asia/Hong_Kong",    # 香港 UTC+8
            "Asia/Singapore",    # シンガポール UTC+8
            "Asia/Bangkok",      # タイ UTC+7
            "Asia/Jakarta",      # インドネシア UTC+7
            "Asia/Kolkata",      # インド UTC+5:30
            "Asia/Dubai",        # UAE UTC+4
            "Europe/Paris",      # フランス UTC+1/+2
            "Europe/Berlin",     # ドイツ UTC+1/+2
            "Europe/London",     # 英国 UTC+0/+1
            "UTC",               # 協定世界時 UTC+0
            "America/New_York",  # 米東部 UTC-5/-4
            "America/Chicago",   # 米中部 UTC-6/-5
            "America/Denver",    # 米山岳 UTC-7/-6
            "America/Los_Angeles", # 米西部 UTC-8/-7
            "Pacific/Honolulu",  # ハワイ UTC-10
        ]
        
        tz_frame = ctk.CTkFrame(scrollable_frame, fg_color="transparent")
        tz_frame.pack(pady=5, padx=40, fill="x")
        ctk.CTkLabel(tz_frame, text="カメラのタイムゾーン:", font=("Yu Gothic UI", 14), width=LABEL_WIDTH, anchor="w").pack(side="left", padx=5)
        default_camera_tz = self.settings.get("camera_timezone", "Asia/Tokyo")
        # デフォルト値がリストにない場合はリストの先頭（Asia/Tokyo）を使用します。
        if default_camera_tz not in TIMEZONE_OPTIONS:
            default_camera_tz = "Asia/Tokyo"
        self.camera_timezone_var = ctk.StringVar(value=default_camera_tz)
        self.camera_timezone_menu = ctk.CTkOptionMenu(
            tz_frame,
            values=TIMEZONE_OPTIONS,
            variable=self.camera_timezone_var,
            font=("Yu Gothic UI", 14),
            width=250
        )
        self.camera_timezone_menu.pack(side="left", padx=5)
        ctk.CTkLabel(tz_frame, text="(デフォルト: Asia/Tokyo = 日本時間)", font=("Yu Gothic UI", 11), text_color="gray").pack(side="left", padx=5)
        
        # 保存ボタン
        ctk.CTkButton(
            scrollable_frame,
            text="設定を保存",
            command=self.save_settings,
            font=("Yu Gothic UI", 16),
            width=200,
            height=40,
            fg_color="#2B7DE9"
        ).pack(pady=20)
        
        # ステータスラベル
        self.settings_status_label = ctk.CTkLabel(
            scrollable_frame,
            text="",
            font=("Yu Gothic UI", 12)
        )
        self.settings_status_label.pack(pady=10)
    
    def select_cache_dir(self) -> None:
        """
        キャッシュディレクトリを選択します。
        """
        path = filedialog.askdirectory()
        if path:
            self.cache_dir_entry.delete(0, "end")
            self.cache_dir_entry.insert(0, path)
    
    def save_settings(self) -> None:
        """
        設定を保存します。
        """
        email = self.email_entry.get()
        password = self.password_entry.get()
        cache_dir = self.cache_dir_entry.get()
        custom_extensions = self.extensions_entry.get()
        overwrite_existing_geotag = self.overwrite_existing_geotag_var.get()
        exiftool_max_workers = self.exiftool_max_workers_entry.get().strip()
        map_center_coord = self.map_center_coord_entry.get()
        map_zoom = self.map_zoom_entry.get()
        show_gpx_path = self.show_gpx_path_var.get()
        tile_server_url = self.tile_server_url_entry.get().strip()
        tile_server_max_zoom = self.tile_server_max_zoom_entry.get().strip()
        gpx_track_color = self.gpx_track_color_entry.get().strip()
        gpx_track_width = self.gpx_track_width_entry.get().strip()
        gpx_track_width = self.gpx_track_width_entry.get().strip()
        camera_timezone = self.camera_timezone_var.get().strip()
        
        if not email or not password:
            self.settings_status_label.configure(
                text="❌ ユーザー名とパスワードを入力してください",
                text_color="red"
            )
            return
        
        # 座標のバリデーション
        try:
            # カンマ区切りで緯度経度を分割
            coords = map_center_coord.split(',')
            if len(coords) != 2:
                raise ValueError("座標はカンマで区切って入力してください")
            
            lat = float(coords[0].strip())
            lon = float(coords[1].strip())
            zoom = int(map_zoom)
            
            if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                raise ValueError("座標が範囲外")
            if not (1 <= zoom <= 19):
                raise ValueError("ズーム倍率が範囲外")
        except ValueError as e:
            if "ズーム" in str(e):
                self.settings_status_label.configure(
                    text="❌ ズーム倍率は1～19の整数で入力してください",
                    text_color="red"
                )
            elif "カンマ" in str(e):
                self.settings_status_label.configure(
                    text="❌ 座標は 緯度,経度 の形式で入力してください（例: 35.6812,139.7671）",
                    text_color="red"
                )
            else:
                self.settings_status_label.configure(
                    text="❌ 座標の入力が不正です（緯度: -90～90, 経度: -180～180）",
                    text_color="red"
                )
            return
        
        # ExifTool並列ワーカー数のバリデーション
        try:
            workers_val = int(exiftool_max_workers) if exiftool_max_workers else 4
            if not (1 <= workers_val <= 16):
                raise ValueError("ワーカー数が範囲外")
        except ValueError:
            self.settings_status_label.configure(
                text="❌ ExifTool並列ワーカー数は1～16の整数で入力してください",
                text_color="red"
            )
            return
        
        # タイルサーバー最大ズームのバリデーション
        try:
            max_zoom_val = int(tile_server_max_zoom) if tile_server_max_zoom else 16
            if not (1 <= max_zoom_val <= 22):
                raise ValueError("最大ズームが範囲外")
        except ValueError:
            self.settings_status_label.configure(
                text="❌ タイルサーバー最大ズームは1～22の整数で入力してください",
                text_color="red"
            )
            return
        
        # タイルサーバーURLが空の場合はデフォルト値を使用
        if not tile_server_url:
            tile_server_url = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        
        # GPX軌跡の色のバリデーション
        if gpx_track_color:
            # 16進数カラーコードの形式をチェック
            import re
            if not re.match(r'^#[0-9A-Fa-f]{6}$', gpx_track_color):
                self.settings_status_label.configure(
                    text="❌ 軌跡の色は #RRGGBB 形式で入力してください（例: #FF0000）",
                    text_color="red"
                )
                return
        else:
            gpx_track_color = "#FF0000"  # デフォルト: 赤
        
        # GPX軌跡の幅のバリデーション
        try:
            track_width_val = int(gpx_track_width) if gpx_track_width else 3
            if not (1 <= track_width_val <= 10):
                raise ValueError("幅が範囲外")
        except ValueError:
            self.settings_status_label.configure(
                text="❌ 軌跡の幅は1～10の整数で入力してください",
                text_color="red"
            )
            return
        
        # 変更前の設定を保持（マップ再生成判定用）
        old_cache_dir = self.settings.get("cache_dir", str(SettingsManager._get_default_dir()))
        old_tile_server_url = self.settings.get("tile_server_url", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
        old_tile_server_max_zoom = str(self.settings.get("tile_server_max_zoom", "16"))
        
        SettingsManager.save({
            "email": email,
            "password_encoded": encode_password(password),
            "activity_download_format": self.dl_format_var.get(),
            "cache_dir": cache_dir,
            "custom_extensions": custom_extensions,
            "overwrite_existing_geotag": overwrite_existing_geotag,
            "exiftool_max_workers": workers_val,
            "map_center_lat": lat,
            "map_center_lon": lon,
            "map_zoom": map_zoom,
            "show_gpx_path": show_gpx_path,
            "tile_server_url": tile_server_url,
            "tile_server_max_zoom": max_zoom_val,
            "gpx_track_color": gpx_track_color,
            "gpx_track_width": track_width_val,
            "camera_timezone": camera_timezone
        })
        
        # 設定を再読み込み
        self.settings = SettingsManager.load()
        
        # マップ関連設定が変更された場合、マップウィジェットを再生成
        need_map_regeneration = (
            cache_dir != old_cache_dir or
            tile_server_url != old_tile_server_url or
            str(max_zoom_val) != old_tile_server_max_zoom
        )
        
        if need_map_regeneration:
            self.regenerate_map_widget()
        else:
            # GPXパス表示設定のみ変更された場合は軌跡を更新
            self.update_gpx_paths()
        
        self.settings_status_label.configure(
            text="✓ 設定を保存しました",
            text_color="#2FA84F"
        )
        
        # 3秒後にメッセージをクリア
        self.after(3000, lambda: self.settings_status_label.configure(text=""))
    
    def regenerate_map_widget(self) -> None:
        """
        マップウィジェットを破棄して再生成します。
        キャッシュディレクトリやタイルサーバー設定が変更された場合に呼ばれます。
        """
        # 既存のマーカーとパスをクリア
        for marker in self.map_markers:
            try:
                marker.delete()
            except Exception:
                pass
        self.map_markers.clear()
        
        for path in self.map_paths:
            try:
                path.delete()
            except Exception:
                pass
        self.map_paths.clear()
        
        # right_frame内のウィジェットをすべて破棄
        for widget in self.right_frame.winfo_children():
            widget.destroy()
        
        self.map_widget = None
        self.map_info_label = None
        
        # 地図UIを再構築
        self.setup_map_ui(self.right_frame)
        
        # 既存のマーカーを再配置
        if self.photo_locations:
            self.update_map_markers()
    
    def setup_info_tab(self, parent_frame) -> None:
        """
        情報タブのUIを構築します。
        """
        # スクロール可能なフレームを作成
        scrollable_frame = ctk.CTkScrollableFrame(parent_frame, fg_color="transparent")
        scrollable_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # ソフトウェアアイコン/タイトル
        ctk.CTkLabel(
            scrollable_frame,
            text="📷 GM Photo Tagger",
            font=("Yu Gothic UI", 28, "bold")
        ).pack(pady=(20, 10))
        
        # バージョン情報
        ctk.CTkLabel(
            scrollable_frame,
            text="Version 1.0.0",
            font=("Yu Gothic UI", 16),
            text_color="gray"
        ).pack(pady=(0, 30))
        
        # 説明
        description = (
            "GM Photo Taggerは、Garmin Connectなどからダウンロードした\n"
            "GPXファイルを使用して、撮影した写真に位置情報を\n"
            "自動的に付与するアプリケーションです。\n\n"
            "主な機能:\n"
            "• Garmin ConnectからのGPXファイル自動ダウンロード\n"
            "• ExifToolを使用した高精度なジオタギング\n"
            "• OpenStreetMapでの写真位置表示\n"
            "• RAWファイル対応（CR2, CR3, NEF, ARW, RAF等）\n"
            "• ジオタグ有無でのファイル自動分類"
        )
        
        ctk.CTkLabel(
            scrollable_frame,
            text=description,
            font=("Yu Gothic UI", 14),
            justify="left"
        ).pack(pady=(0, 20))
        
        # 区切り線
        separator0 = ctk.CTkFrame(scrollable_frame, height=2, fg_color="gray")
        separator0.pack(fill="x", pady=20, padx=50)

        # 注意事項セクション
        ctk.CTkLabel(
            scrollable_frame,
            text="⚠️注意事項",
            font=("Yu Gothic UI", 20, "bold"),
            # text_color="#FF6B6B"
        ).pack(pady=(10, 15))
        
        # warnings = (
        #     "【拡張子について】\n"
        #     "• 設定タブの「カスタム拡張子」で指定された拡張子のファイルのみが処理対象です\n"
        #     "• 必要に応じて拡張子を追加することで、様々な形式に対応できます\n"
        #     "• 大文字・小文字は区別されません（.jpg と .JPG は同じ扱いです）\n\n"
        #     "【ExifToolの対応について】\n"
        #     "• 一部の拡張子はExifToolでサポートされていない場合があります\n"
        #     "• サポート外の形式の場合、ジオタグが正常に付与されない可能性があります\n\n"
        #     "【動画ファイルについて】\n"
        #     "• .mov, .mp4 などの動画ファイルにもジオタグを付与できます\n"
        #     "• 動画ファイルは処理に時間がかかる場合があります\n"
        #     "• 動画ファイルは地図上ではサムネイルが正しく表示されない場合があります\n\n"
        #     "【⚠️ ジオタグ付与後の確認について】\n"
        #     "• 処理が完了しても、必ずLightroom、Capture One、Adobe Bridge等の編集ソフトで実際にファイルが正しく開けることを確認してください\n"
        #     "• アプリケーション上で「完了」と表示されても、ファイル形式などによってはファイルが破損する場合が可能性があります\n"
        #     "• GPS情報が正しく表示されるか、複数のファイルで確認してください\n"
        #     "• 確認が完了するまで、元のファイルを削除しないでください\n\n"
        #     "【データの安全性について】\n"
        #     "• 画像の取り込みはコピーによって行われます（元ファイルは保持されます）\n"
        #     "• ただし、万が一画像が破損した場合でも、本ソフトウェアの作者は一切の責任を負うことができません\n"
        #     "• 重要なデータは必ずバックアップを取ってからご使用ください"
        # )

        warnings = (
            "【拡張子について】\n"
            "• 設定タブの「カスタム拡張子」で指定された拡張子のファイルのみが処理対象です\n"
            "• 必要に応じて拡張子を追加することで、様々な形式に対応できます\n"
            "• 大文字・小文字は区別されません（.jpg と .JPG は同じ扱いです）\n\n"
            "【ExifToolの対応について】\n"
            "• 一部の拡張子は ExifTool でサポートされていない場合があります\n"
            "• サポート外の形式では、ジオタグが正常に付与されない、またはファイルが期待通りに扱えない可能性があります\n\n"
            "【動画ファイルについて】\n"
            "• .mov, .mp4 などの動画ファイルにもジオタグを付与できます\n"
            "• 動画ファイルは処理に時間がかかる場合があります\n"
            "• 動画ファイルは地図上ではサムネイルが正しく表示されない場合があります\n\n"
            "【Garmin Connect について】\n"
            "• 本機能は Garmin 社の公式 SDK ではなく、サードパーティ製ライブラリを利用して Garmin Connect にアクセスします\n"
            "• Garmin Connect 側の仕様変更、認証方式の変更、レート制限、アカウント状態などにより、予告なく動作しなくなる可能性があります\n"
            "• 利用にあたっては、関連する利用条件等を各自で確認し、自己責任でご使用ください\n"
            "• 認証情報を保存する場合は、共用 PC での利用を避け、漏えいが疑われる場合は速やかにパスワード変更等を行ってください\n\n"
            "【地図タイル利用について】\n"
            "• OpenStreetMap の標準タイルサーバーは無保証で提供されており、過剰利用時には予告なく制限または遮断される可能性があります\n"
            "• 大量アクセス、prefetch、offline 用の一括取得は行わないでください\n"
            "• 商用利用や継続的な大量利用が想定される場合は、代替のタイルプロバイダまたは自前ホスティングを検討してください\n\n"
            "【ジオタグ付与後の確認について】\n"
            "• 処理が完了しても、必ず Lightroom、Capture One、Adobe Bridge 等の編集ソフトで実際にファイルが正しく開けることを確認してください\n"
            "• アプリケーション上で「完了」と表示されても、ファイル形式や外部ツールの挙動によっては、メタデータ不整合や破損が生じる可能性があります\n"
            "• GPS 情報が正しく表示されるか、複数のファイルで確認してください\n"
            "• 確認が完了するまで、元のファイルを削除しないでください\n\n"
            "【免責事項】\n"
            "• 画像の取り込みはコピーによって行われます（元ファイルは保持されます）\n"
            "• 重要なデータは、必ず事前にバックアップを作成してください\n"
            "• 本ソフトウェアの利用により、ファイル破損、読込不能、メタデータ不整合、データ損失等が発生する可能性があります\n"
            "• 作者は、本ソフトウェアの利用または利用不能により生じた損害について、法令上許される範囲で責任を負いません"
        )
        
        warning_box = ctk.CTkTextbox(
            scrollable_frame,
            width=700,
            height=400,
            font=("Yu Gothic UI", 12),
            fg_color="#FFFACD",
            text_color="#000000"
        )
        warning_box.pack(pady=(0, 20), padx=30)
        warning_box.insert("1.0", warnings)
        warning_box.configure(state="disabled")
        
        # 区切り線
        separator1 = ctk.CTkFrame(scrollable_frame, height=2, fg_color="gray")
        separator1.pack(fill="x", pady=20, padx=50)
        
        # 作成者情報
        ctk.CTkLabel(
            scrollable_frame,
            text="開発者情報",
            font=("Yu Gothic UI", 20, "bold")
        ).pack(pady=(10, 15))
        
        ctk.CTkLabel(
            scrollable_frame,
            text="作成者: さんだ～",
            font=("Yu Gothic UI", 20)
        ).pack(pady=5)
        
        # ロゴ画像をクリックするとXプロフィールへジャンプ
        # light_image: ライトモード用（黒ロゴ）、dark_image: ダークモード用（白ロゴ）
        _logo_black_path = Path(__file__).parent / "static" / "logo" / "logo-black.png"
        _logo_white_path = Path(__file__).parent / "static" / "logo" / "logo-white.png"
        _logo_img = ctk.CTkImage(
            light_image=Image.open(_logo_black_path),
            dark_image=Image.open(_logo_white_path),
            size=(30, 30)
        )
        
        # Xアイコンとアカウント名を横並びに表示
        handle_frame = ctk.CTkFrame(scrollable_frame, fg_color="transparent")
        handle_frame.pack(pady=(10, 10))
        
        _logo_label = ctk.CTkLabel(
            handle_frame,
            image=_logo_img,
            text="",
            cursor="hand2"
        )
        _logo_label.bind("<Button-1>", lambda e: self.open_twitter_link())
        _logo_label.pack(side="left", padx=(0, 5))
        
        # アカウント名テキスト（クリック可能）
        _handle_label = ctk.CTkLabel(
            handle_frame,
            text="@erraticradar_01",
            font=("Yu Gothic UI", 20),
            text_color="#1DA1F2",
            cursor="hand2"
        )
        _handle_label.bind("<Button-1>", lambda e: self.open_twitter_link())
        _handle_label.pack(side="left")
        
        # 区切り線
        separator2 = ctk.CTkFrame(scrollable_frame, height=2, fg_color="gray")
        separator2.pack(fill="x", pady=20, padx=50)
        
        # フッター
        ctk.CTkLabel(
            scrollable_frame,
            text="© 2026 さんだ～ / GM Photo Tagger",
            font=("Yu Gothic UI", 11),
            text_color="gray"
        ).pack(pady=(30, 20))
    
    def open_twitter_link(self) -> None:
        """
        X（旧Twitter）プロフィールページをブラウザで開きます。
        """
        import webbrowser
        webbrowser.open("https://x.com/erraticradar_01")


    def start_download(self) -> None:
        """
        ダウンロード処理を別スレッドで開始します。
        """
        # スキャンした日付がある場合はそれを使用、なければ手動選択の日付を使用
        if self.selected_dates:
            # selected_datesをリストに変換してダウンロード
            dates_list = sorted(self.selected_dates)
            DownloadLogPopup(self, dates_list[0], dates_list[-1], selected_dates=dates_list)
        elif self.start_date:
            # ログ表示ポップアップを開く
            DownloadLogPopup(self, self.start_date, self.end_date)
        else:
            messagebox.showwarning("確認", "日付を選択してください。")
            return


if __name__ == "__main__":
    # CustomTkinterのテーマ設定
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")

    app = MainApp()
    app.mainloop()
