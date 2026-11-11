"""Command line entry point: ``magicdb [serve | update]``."""

import argparse
from pathlib import Path

import uvicorn

from magicdb import __version__
from magicdb.app import create_app
from magicdb.catalog import ingest
from magicdb.config import ConfigError, Settings, load_settings


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="magicdb", description="magic-db server")
    parser.add_argument("--version", action="version", version=f"magic-db {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the web server (default)")
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
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        parser.exit(2, f"magicdb: {exc}\n")

    if args.command == "update":
        return _update(settings, args)

    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
