"""SQLite connections and collection migrations (SPEC §5).

A connection opens ``collection.db`` as the main database and attaches the live catalog
read-only as ``cat``: unqualified table names are the collection, ``cat.printings`` and
friends are the catalog.
"""

import re
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Request

from magicdb.catalog import publish
from magicdb.config import Settings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATION_NAME = re.compile(r"^(\d{4})_.+\.sql$")


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _migrations() -> list[tuple[int, Path]]:
    found = []
    for path in MIGRATIONS_DIR.iterdir():
        match = _MIGRATION_NAME.match(path.name)
        if match:
            found.append((int(match.group(1)), path))
    return sorted(found)


def _backup_before_migration(conn: sqlite3.Connection, settings: Settings) -> Path:
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = settings.backup_dir / f"pre-migration-{stamp}.db"
    with closing(sqlite3.connect(target)) as copy:
        conn.backup(copy)
    return target


def migrate(settings: Settings) -> int:
    """Create or upgrade ``collection.db``; return its schema version."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(settings.collection_path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        pending = [(number, path) for number, path in _migrations() if number > version]
        if pending and version > 0:
            _backup_before_migration(conn, settings)
        for number, path in pending:
            sql = path.read_text(encoding="utf-8")
            # number comes from the file name pattern (digits only), never from user input
            conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
            version = number
    return version


def connect(settings: Settings) -> sqlite3.Connection:
    """Open the collection and attach the live catalog, if one exists, as ``cat``."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(settings.collection_path), uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    live = publish.live_catalog(settings)
    if live is not None:
        conn.execute("ATTACH DATABASE ? AS cat", (publish.read_only_uri(live),))
    return conn


def has_catalog(conn: sqlite3.Connection) -> bool:
    return any(row[1] == "cat" for row in conn.execute("PRAGMA database_list"))


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: one short-lived connection per request."""
    conn = connect(request.app.state.settings)
    try:
        yield conn
    finally:
        conn.close()
