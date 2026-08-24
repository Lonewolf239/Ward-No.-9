import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(SPEC)))
sys.path.insert(0, ROOT)

from game import settings as S

APP_SLUG = os.environ.get("WARD9_APP_SLUG", "ward9")
VERSION_FILE = os.environ.get("WARD9_VERSION_FILE") or None
ICON_PATH = os.path.join(ROOT, "icon.ico")
ICON_PATH_MAC = os.path.join(ROOT, "icon.icns")

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (os.path.join(ROOT, "game", "assets"), os.path.join("game", "assets")),
        (os.path.join(ROOT, "game", "room_data"), os.path.join("game", "room_data")),
        (os.path.join(ROOT, "game", "zone_data"), os.path.join("game", "zone_data")),
        (os.path.join(ROOT, "game", "locale_data"), os.path.join("game", "locale_data")),
    ],
    hiddenimports=[
        # main.py only imports this lazily (inside the app<->editor mode
        # dispatcher, App._open_room_editor's "open editor" path) so it can
        # be reached from the in-game main menu button - spelled out here
        # since PyInstaller's static import scan can miss deferred imports.
        "tools.room_editor.editor",
        "tools.room_editor.panel",
        "tools.room_editor.grid_view",
        "tools.room_editor.raycast",
        "tools.room_editor.camera",
        "tools.room_editor.room_model",
        "tools.room_editor.zone_model",
        "tkinter",
        "tkinter.filedialog",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_SLUG,
    console=False,
    icon=ICON_PATH,
    version=VERSION_FILE,
)

if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name=f"{APP_SLUG}.app",
        icon=ICON_PATH_MAC,
        bundle_identifier=f"com.lonewolf239.{APP_SLUG}",
        info_plist={
            "CFBundleName": S.TITLE,
            "CFBundleDisplayName": S.TITLE,
            "CFBundleShortVersionString": S.VERSION,
            "CFBundleVersion": S.VERSION,
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": f"{S.TITLE} {S.VERSION}",
        },
    )