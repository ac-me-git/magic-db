"""Build the single-file Windows executable with PyInstaller (SPEC §10).

    uv pip install pyinstaller      (or: pip install pyinstaller)
    python scripts/build_exe.py

Writes dist/magic-db.exe. The release workflow runs this on every tag.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ARGUMENTS = [
    "--noconfirm",
    "--clean",
    "--onefile",
    "--console",  # the window shows the address and stops the server when closed
    "--name",
    "magic-db",
    # templates, static files, translations, SQL: everything in the package that is not code
    "--collect-data",
    "magicdb",
    # these pick their implementation at run time, where PyInstaller cannot see it
    "--collect-submodules",
    "uvicorn",
    "--collect-submodules",
    "ijson",
    "--collect-submodules",
    "rapidfuzz",
    "--hidden-import",
    "python_multipart",
    "--distpath",
    str(ROOT / "dist"),
    "--workpath",
    str(ROOT / "build"),
    "--specpath",
    str(ROOT / "build"),
    str(ROOT / "packaging" / "launcher.py"),
]


def main() -> int:
    return subprocess.call([sys.executable, "-m", "PyInstaller", *ARGUMENTS], cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
