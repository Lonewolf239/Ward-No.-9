from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from game import settings as S

APP_SLUG = "ward9"

if sys.platform.startswith("win"):
    PLATFORM_TAG = "windows"
elif sys.platform == "darwin":
    PLATFORM_TAG = "macos"
else:
    PLATFORM_TAG = "linux"


def _write_windows_version_file(path):
    from PyInstaller.utils.win32 import versioninfo as vi

    filevers = (0, 1, 0, 0)
    info = vi.VSVersionInfo(
        ffi=vi.FixedFileInfo(filevers=filevers, prodvers=filevers, mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
        kids=[
            vi.StringFileInfo([
                vi.StringTable("040904B0", [
                    vi.StringStruct("CompanyName", "Lonewolf239"),
                    vi.StringStruct("FileDescription", S.TITLE),
                    vi.StringStruct("FileVersion", S.VERSION),
                    vi.StringStruct("InternalName", APP_SLUG),
                    vi.StringStruct("OriginalFilename", f"{APP_SLUG}.exe"),
                    vi.StringStruct("ProductName", S.TITLE),
                    vi.StringStruct("ProductVersion", S.VERSION),
                ]),
            ]),
            vi.VarFileInfo([vi.VarStruct("Translation", [1033, 1200])]),
        ],
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(info))


def main():
    dist_dir = os.path.join(ROOT, "publish", PLATFORM_TAG)
    work_dir = os.path.join(ROOT, "build", PLATFORM_TAG)
    shutil.rmtree(dist_dir, ignore_errors=True)
    os.makedirs(dist_dir, exist_ok=True)

    env = os.environ.copy()
    env["WARD9_APP_SLUG"] = APP_SLUG

    if sys.platform.startswith("win"):
        version_file = os.path.join(ROOT, "packaging", "_version_info.txt")
        _write_windows_version_file(version_file)
        env["WARD9_VERSION_FILE"] = version_file

    spec_path = os.path.join(ROOT, "packaging", "ward9.spec")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        spec_path,
        "--noconfirm",
        "--distpath", dist_dir,
        "--workpath", work_dir,
    ]
    subprocess.run(cmd, cwd=ROOT, env=env, check=True)

    produced = sorted(os.listdir(dist_dir))
    print(f"\nBuilt for {PLATFORM_TAG}: {', '.join(produced)}")
    print(f"-> {dist_dir}")


if __name__ == "__main__":
    main()
