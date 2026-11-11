"""Versioned catalog files: find the live one, validate and publish a new one (SPEC §4.2).

The live catalog is the newest ``cards-<timestamp>.db`` in the catalog directory. A file
that may be in use is never overwritten or deleted, which is what makes the swap work on
Windows; old files are removed later, on a best-effort basis.
"""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from magicdb.config import Settings

PATTERN = "cards-*.db"
STAMP = "%Y%m%dT%H%M%S"


def read_only_uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro"


def live_catalog(settings: Settings) -> Path | None:
    """Newest catalog file, or None when the catalog has never been built."""
    files = sorted(settings.catalog_dir.glob(PATTERN))
    return files[-1] if files else None


def read_meta(path: Path) -> dict[str, str]:
    with closing(sqlite3.connect(read_only_uri(path), uri=True)) as conn:
        return dict(conn.execute("SELECT key, value FROM meta"))


def check(new: Path, live: Path | None, min_rows: int) -> str | None:
    """Return why ``new`` must not be published, or None when it is sound."""
    with closing(sqlite3.connect(read_only_uri(new), uri=True)) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            return f"integrity check failed: {integrity}"
        rows = conn.execute("SELECT COUNT(*) FROM printings").fetchone()[0]
    if rows <= min_rows:
        return f"only {rows} printings, expected more than {min_rows}"
    if live is not None:
        live_rows = int(read_meta(live).get("row_count", "0"))
        if rows < live_rows * 0.9:
            return f"only {rows} printings, the live catalog has {live_rows}"
    return None


def publish(new: Path, settings: Settings) -> Path:
    """Move ``new`` into the catalog directory under a name newer than every other."""
    settings.catalog_dir.mkdir(parents=True, exist_ok=True)
    moment = datetime.now(UTC)
    live = live_catalog(settings)
    while True:
        target = settings.catalog_dir / f"cards-{moment.strftime(STAMP)}.db"
        if not target.exists() and (live is None or target.name > live.name):
            break
        moment += timedelta(seconds=1)
    new.replace(target)
    return target


def cleanup_old(settings: Settings) -> list[Path]:
    """Delete every catalog file but the live one; skip files still in use."""
    removed: list[Path] = []
    for old in sorted(settings.catalog_dir.glob(PATTERN))[:-1]:
        try:
            old.unlink()
        except OSError:
            continue  # open somewhere (Windows): retried on the next run and at startup
        removed.append(old)
    return removed
