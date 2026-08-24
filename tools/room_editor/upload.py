import io
import json
import os
import urllib.error
import urllib.request
import zipfile

UPLOAD_URL = os.environ.get("WARD9_UPLOAD_URL", "")
UPLOAD_KEY = os.environ.get("WARD9_UPLOAD_KEY", "")
if not UPLOAD_URL or not UPLOAD_KEY:
    try:
        from game._upload_config import UPLOAD_URL as _BAKED_URL, UPLOAD_KEY as _BAKED_KEY
        UPLOAD_URL = UPLOAD_URL or _BAKED_URL
        UPLOAD_KEY = UPLOAD_KEY or _BAKED_KEY
    except ImportError:
        pass

_OBFUSCATION_KEY = b"ward9-room-upload-obfuscation-v1"


def _xor(data, key):
    if not key:
        return data
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def _settings_path():
    from game.app import SETTINGS_PATH, _LEGACY_SETTINGS_PATH
    return SETTINGS_PATH if SETTINGS_PATH.exists() else _LEGACY_SETTINGS_PATH


def get_saved_nickname():
    try:
        with open(_settings_path(), encoding="utf-8") as f:
            return json.load(f).get("editor_nickname") or None
    except (OSError, ValueError):
        return None


def save_nickname(nickname):
    from game.app import SETTINGS_PATH
    path = SETTINGS_PATH
    data = {}
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
    data["editor_nickname"] = nickname
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = str(path) + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def build_upload_zip(model_json_dict, model_filename, nickname, jpeg_bytes_list):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(model_filename, json.dumps(model_json_dict, ensure_ascii=False, indent=2))
        zf.writestr("creator.txt", nickname)
        for i, jpg in enumerate(jpeg_bytes_list):
            zf.writestr(f"preview_{i + 1}.jpg", jpg)
    return _xor(buf.getvalue(), _OBFUSCATION_KEY)


MAX_ENTRIES = 20
MAX_ENTRY_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 24 * 1024 * 1024


def _read_capped(zf, name, max_bytes):
    with zf.open(name) as f:
        data = f.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"'{name}' exceeds the {max_bytes}-byte per-file limit")
    return data


def unpack_upload_zip(blob):
    raw = _xor(blob, _OBFUSCATION_KEY)
    out = {"model_filename": None, "model_json": None, "nickname": None, "jpegs": []}
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
        if len(names) > MAX_ENTRIES:
            raise ValueError(f"archive has too many entries ({len(names)})")
        total = 0
        for name in names:
            if name == "creator.txt":
                data = _read_capped(zf, name, MAX_ENTRY_BYTES)
                out["nickname"] = data.decode("utf-8", errors="replace")[:64]
            elif name.startswith("preview_") and name.endswith(".jpg"):
                data = _read_capped(zf, name, MAX_ENTRY_BYTES)
                out["jpegs"].append(data)
            elif name.endswith(".json"):
                data = _read_capped(zf, name, MAX_ENTRY_BYTES)
                out["model_filename"] = name
                out["model_json"] = json.loads(data)
            else:
                continue
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise ValueError("archive contents exceed the total size limit")
    return out


INCOMING_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "incoming_uploads")


def list_incoming():
    if not os.path.isdir(INCOMING_DIR):
        return []
    return sorted(f for f in os.listdir(INCOMING_DIR) if f.lower().endswith(".zip"))


def upload(data, kind, item_id):
    if not UPLOAD_URL or not UPLOAD_KEY:
        return False, "upload endpoint not configured in this build"
    req = urllib.request.Request(
        UPLOAD_URL, data=data, method="POST",
        headers={
            "Content-Type": "application/octet-stream",
            "X-Upload-Key": UPLOAD_KEY,
            "X-Item-Kind": kind,
            "X-Item-Id": item_id,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return 200 <= resp.status < 300, None
    except (urllib.error.URLError, OSError) as e:
        return False, str(e)
