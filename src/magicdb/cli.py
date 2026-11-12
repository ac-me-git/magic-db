"""Command line entry point: ``magicdb [serve | update]``.

The same function starts the Docker container, ``uv run magicdb`` and the Windows
executable. Run as an executable (SPEC §10) it keeps its data next to itself and opens
the browser, so that a double click is all it takes.
"""

import argparse
import os
import sys
import threading
import webbrowser
from collections.abc import Mapping
from pathlib import Path

import uvicorn

from magicdb import __version__
from magicdb.app import create_app
from magicdb.catalog import ingest
from magicdb.config import ConfigError, Settings, load_settings

BROWSER_DELAY = 1.5  # seconds: long enough for the server to be listening


def is_executable() -> bool:
    """True when running as the packaged Windows executable (PyInstaller)."""
    return bool(getattr(sys, "frozen", False))


def environment(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The settings' environment. The executable defaults to a ``data`` folder beside
    itself instead of one in whatever the current directory happens to be."""
    values = dict(os.environ if env is None else env)
    if is_executable() and not values.get("MAGICDB_DATA_DIR", "").strip():
        values["MAGICDB_DATA_DIR"] = str(Path(sys.executable).resolve().parent / "data")
    return values


def local_url(settings: Settings) -> str:
    """Where a browser on this computer finds the server."""
    host = "127.0.0.1" if settings.host in ("0.0.0.0", "::") else settings.host
    return f"http://{host}:{settings.port}/"


def _update(settings: Settings, args: argparse.Namespace) -> int:
    result = ingest.run_update(
        settings,
        from_file=args.from_file,
        sets_file=args.sets_file,
        force=args.force,
        min_rows=args.min_rows,
    )
    print(f"catalog update: {result.status} - {result.message}")
    return 1 if result.status == "error" else 0


def _serve(settings: Settings, open_browser: bool) -> int:
    url = local_url(settings)
    print(f"magic-db {__version__}")
    print(f"  open      {url}")
    print(f"  data in   {settings.data_dir.resolve()}")
    print("  to stop   close this window, or press Ctrl+C")
    if open_browser:
        timer = threading.Timer(BROWSER_DELAY, webbrowser.open, [url])
        timer.daemon = True
        timer.start()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="magicdb", description="magic-db server")
    parser.add_argument("--version", action="version", version=f"magic-db {__version__}")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="run the web server (default)")
    for target in (parser, serve):
        target.add_argument(
            "--browser",
            action=argparse.BooleanOptionalAction,
            default=None,
            help="open the browser once the server is up (default: only for the executable)",
        )
    update = sub.add_parser("update", help="download the card catalog from Scryfall now")
    update.add_argument(
        "--from-file", type=Path, help="build from a local bulk JSON file instead of downloading"
    )
    update.add_argument("--sets-file", type=Path, help="sets JSON to use with --from-file")
    update.add_argument(
        "--force", action="store_true", help="rebuild even if Scryfall has nothing new"
    )
    update.add_argument(
        "--min-rows",
        type=int,
        default=ingest.MIN_ROWS,
        help="reject a catalog with this many printings or fewer (default: %(default)s)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        settings = load_settings(environment())
    except ConfigError as exc:
        parser.exit(2, f"magicdb: {exc}\n")

    if args.command == "update":
        return _update(settings, args)
    return _serve(settings, is_executable() if args.browser is None else args.browser)


def run_executable() -> int:
    """Entry point of the packaged executable.

    A double-clicked program that fails must not just vanish: say what went wrong and
    wait, so the window stays open long enough to read it.
    """
    try:
        return main()
    except KeyboardInterrupt:
        return 0
    except BaseException as error:  # SystemExit included: argparse and uvicorn use it
        if isinstance(error, SystemExit) and error.code in (0, None):
            return 0
        print(f"\nmagic-db could not start: {error!r}", file=sys.stderr)
        print("Is another copy already running, or the port in use?", file=sys.stderr)
        try:
            input("Press Enter to close this window.")
        except EOFError:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
