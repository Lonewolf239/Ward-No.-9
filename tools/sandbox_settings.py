import atexit
import io
import json
import os
import shutil
import tempfile


def redirect(**overrides):
    import game.app as A

    tmp = tempfile.mkdtemp(prefix="ward9-tool-")
    atexit.register(shutil.rmtree, tmp, True)
    path = os.path.join(tmp, "settings.json")
    if A.SETTINGS_PATH.exists():
        shutil.copy(str(A.SETTINGS_PATH), path)
    data = {}
    if os.path.exists(path):
        try:
            data = json.load(io.open(path, encoding="utf-8"))
        except ValueError:
            data = {}
    data["fullscreen"] = False
    data.update(overrides)
    io.open(path, "w", encoding="utf-8").write(json.dumps(data))
    A.SETTINGS_PATH = A.Path(path)
    return path
