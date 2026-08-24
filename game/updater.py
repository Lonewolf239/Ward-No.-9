import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile

from game import settings as S

GITHUB_REPO = "Lonewolf239/Ward-No.-9"
API_LATEST_RELEASE = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_REQUEST_TIMEOUT = 10.0
_DOWNLOAD_CHUNK = 1 << 16

PLATFORM_TAG = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
ASSET_NAME = f"ward9-{PLATFORM_TAG}.zip"


class UpdateChecker:

    def __init__(self):
        self._lock = threading.Lock()
        self._state = "idle"
        self._latest_tag = None
        self._release_notes = None
        self._download_url = None
        self._error = None
        self._progress = 0.0
        self._downloaded_path = None
        self._downloaded_bytes = 0
        self._total_bytes = 0
        self._speed_bps = 0.0

    def snapshot(self):
        with self._lock:
            return dict(
                state=self._state, latest_tag=self._latest_tag, release_notes=self._release_notes,
                error=self._error, progress=self._progress, downloaded_path=self._downloaded_path,
                downloaded_bytes=self._downloaded_bytes, total_bytes=self._total_bytes,
                speed_bps=self._speed_bps,
            )

    def start_check(self):
        with self._lock:
            if self._state in ("checking", "downloading"):
                return
            self._state = "checking"
        threading.Thread(target=self._run_check, daemon=True).start()

    def _run_check(self):
        try:
            req = urllib.request.Request(API_LATEST_RELEASE, headers={"Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                data = json.load(resp)
            tag = data.get("tag_name")
            notes = data.get("body") or ""
            asset_url = None
            for asset in data.get("assets", []):
                if asset.get("name") == ASSET_NAME:
                    asset_url = asset.get("browser_download_url")
                    break
            with self._lock:
                if not tag or tag == S.VERSION:
                    self._state = "none"
                elif asset_url is None:
                    self._state = "error"
                    self._error = f"release {tag} has no {ASSET_NAME} asset"
                else:
                    self._state = "available"
                    self._latest_tag = tag
                    self._release_notes = notes
                    self._download_url = asset_url
        except urllib.error.HTTPError as e:
            with self._lock:
                if e.code == 404:
                    self._state = "none"
                else:
                    self._state = "error"
                    self._error = str(e)
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as e:
            with self._lock:
                self._state = "error"
                self._error = str(e)

    def start_download(self):
        with self._lock:
            if self._state != "available" or not self._download_url:
                return
            url = self._download_url
            self._state = "downloading"
            self._progress = 0.0
            self._downloaded_bytes = 0
            self._total_bytes = 0
            self._speed_bps = 0.0
        threading.Thread(target=self._run_download, args=(url,), daemon=True).start()

    def _run_download(self, url):
        try:
            tmp_dir = tempfile.mkdtemp(prefix="ward9_update_")
            tmp_path = os.path.join(tmp_dir, ASSET_NAME)
            req = urllib.request.Request(url, headers={"Accept": "application/octet-stream"})
            start_time = time.monotonic()
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                total = int(resp.headers.get("Content-Length", 0)) or None
                with self._lock:
                    self._total_bytes = total or 0
                read = 0
                with open(tmp_path, "wb") as f:
                    while True:
                        chunk = resp.read(_DOWNLOAD_CHUNK)
                        if not chunk:
                            break
                        f.write(chunk)
                        read += len(chunk)
                        elapsed = time.monotonic() - start_time
                        with self._lock:
                            self._downloaded_bytes = read
                            self._speed_bps = read / elapsed if elapsed > 0 else 0.0
                            if total:
                                self._progress = min(1.0, read / total)
            if os.path.getsize(tmp_path) == 0 or not zipfile.is_zipfile(tmp_path):
                raise ValueError("downloaded file is not a valid zip")
            with self._lock:
                self._state = "downloaded"
                self._progress = 1.0
                self._downloaded_path = tmp_path
        except (urllib.error.URLError, OSError, ValueError) as e:
            with self._lock:
                self._state = "error"
                self._error = str(e)


def apply_update_and_restart(zip_path):
    if not getattr(sys, "frozen", False):
        raise RuntimeError("apply_update_and_restart only makes sense in a packaged (frozen) build")

    if sys.platform == "darwin":
        _apply_update_macos(zip_path)
    elif sys.platform.startswith("win"):
        _apply_update_windows(zip_path)
    else:
        _apply_update_linux(zip_path)


def cleanup_backup():
    if not getattr(sys, "frozen", False):
        return
    try:
        if sys.platform == "darwin":
            current_exe = os.path.realpath(sys.executable)
            bundle = current_exe
            while bundle and not bundle.endswith(".app"):
                parent = os.path.dirname(bundle)
                if parent == bundle:
                    return
                bundle = parent
            backup_path = bundle + ".bak"
            if os.path.isdir(backup_path):
                shutil.rmtree(backup_path)
        else:
            backup_path = os.path.realpath(sys.executable) + ".bak"
            if os.path.isfile(backup_path):
                os.remove(backup_path)
    except OSError:
        pass


def _extract_single_root(zip_path, dest_dir):
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest_dir)


def _apply_update_linux(zip_path):
    current_exe = os.path.realpath(sys.executable)
    extract_dir = tempfile.mkdtemp(prefix="ward9_update_extract_")
    _extract_single_root(zip_path, extract_dir)
    new_exe = _find_new_binary(extract_dir, current_exe)
    backup_path = current_exe + ".bak"
    shutil.copy2(current_exe, backup_path)
    shutil.copy2(new_exe, current_exe)
    os.chmod(current_exe, os.stat(current_exe).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    os.execv(current_exe, [current_exe])


def _apply_update_macos(zip_path):
    current_exe = os.path.realpath(sys.executable)
    app_bundle = current_exe
    while app_bundle and not app_bundle.endswith(".app"):
        parent = os.path.dirname(app_bundle)
        if parent == app_bundle:
            raise RuntimeError("could not locate the enclosing .app bundle")
        app_bundle = parent
    extract_dir = tempfile.mkdtemp(prefix="ward9_update_extract_")
    _extract_single_root(zip_path, extract_dir)
    new_bundle = None
    for name in os.listdir(extract_dir):
        if name.endswith(".app"):
            new_bundle = os.path.join(extract_dir, name)
            break
    if new_bundle is None:
        raise RuntimeError("downloaded archive has no .app bundle")
    backup_path = app_bundle + ".bak"
    if os.path.exists(backup_path):
        shutil.rmtree(backup_path)
    shutil.move(app_bundle, backup_path)
    shutil.move(new_bundle, app_bundle)
    subprocess.Popen(["open", app_bundle])
    sys.exit(0)


def _apply_update_windows(zip_path):
    current_exe = os.path.realpath(sys.executable)
    extract_dir = tempfile.mkdtemp(prefix="ward9_update_extract_")
    _extract_single_root(zip_path, extract_dir)
    new_exe = _find_new_binary(extract_dir, current_exe)
    backup_path = current_exe + ".bak"
    pid = os.getpid()
    bat_path = os.path.join(extract_dir, "ward9_update.bat")
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(
            "@echo off\r\n"
            f":wait\r\n"
            f"tasklist /FI \"PID eq {pid}\" | find \"{pid}\" >nul\r\n"
            "if not errorlevel 1 (\r\n"
            "  timeout /t 1 /nobreak >nul\r\n"
            "  goto wait\r\n"
            ")\r\n"
            f"copy /Y \"{current_exe}\" \"{backup_path}\" >nul\r\n"
            f"copy /Y \"{new_exe}\" \"{current_exe}\" >nul\r\n"
            f"start \"\" \"{current_exe}\"\r\n"
            "del \"%~f0\"\r\n"
        )
    subprocess.Popen(["cmd", "/c", bat_path], creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    sys.exit(0)


def _find_new_binary(extract_dir, current_exe):
    target_name = os.path.basename(current_exe)
    for root, _dirs, files in os.walk(extract_dir):
        if target_name in files:
            return os.path.join(root, target_name)
    raise RuntimeError(f"downloaded archive has no {target_name}")
