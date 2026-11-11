"""Command line entry point: ``magicdb [serve]``."""

import argparse

import uvicorn

from magicdb import __version__
from magicdb.app import create_app
from magicdb.config import ConfigError, load_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="magicdb", description="magic-db server")
    parser.add_argument("--version", action="version", version=f"magic-db {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the web server (default)")
    parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        parser.exit(2, f"magicdb: {exc}\n")

    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
