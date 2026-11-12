"""What the Windows executable runs. Built by scripts/build_exe.py."""

from magicdb.cli import run_executable

if __name__ == "__main__":
    raise SystemExit(run_executable())
