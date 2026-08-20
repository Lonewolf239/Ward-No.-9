import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(SPEC)))
sys.path.insert(0, ROOT)

from game import settings as S

APP_SLUG = os.environ.get("WARD9_APP_SLUG", "ward9")
VERSION_FILE = os.environ.get("WARD9_VERSION_FILE") or None

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (os.path.join(ROOT, "game", "assets"), os.path.join("game", "assets")),
        (os.path.join(ROOT, "game", "room_data"), os.path.join("game", "room_data")),
    ],
    hiddenimports=[],
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
    version=VERSION_FILE,
)

if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name=f"{APP_SLUG}.app",
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
