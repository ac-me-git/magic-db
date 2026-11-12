"""Collection database: migrations and connections (SPEC §5)."""

import sqlite3
from contextlib import closing

import pytest

from magicdb import db

ENTRY = (
    "INSERT INTO entries (scryfall_id, oracle_id, name, set_code, collector_number, quantity,"
    " finish, condition, location_id, created_at, updated_at)"
    " VALUES ('p1', 'o1', 'Lightning Bolt', 'lea', '161', ?, ?, ?, ?, 'now', 'now')"
)


@pytest.fixture
def conn(settings):
    db.migrate(settings)
    with closing(db.connect(settings)) as connection:
        yield connection


def test_migrate_creates_the_schema_once(settings):
    latest = len(list(db.MIGRATIONS_DIR.glob("[0-9]*.sql")))
    assert db.migrate(settings) == latest
    assert db.migrate(settings) == latest  # nothing left to apply
    with closing(db.connect(settings)) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
        assert {"locations", "entries", "history", "job_runs", "settings"} <= tables
        assert conn.execute("PRAGMA user_version").fetchone()[0] == latest
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert not settings.backup_dir.exists()  # a brand-new database needs no backup


def test_app_startup_migrates(client, settings):
    assert settings.collection_path.exists()


def test_connection_without_catalog(conn):
    assert not db.has_catalog(conn)
    assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0


def test_connection_attaches_catalog_read_only(catalog):
    with closing(db.connect(catalog)) as conn:
        assert db.has_catalog(conn)
        assert conn.execute("SELECT COUNT(*) FROM cat.printings").fetchone()[0] > 0
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM cat.printings")


def test_entry_constraints(conn):
    conn.execute(ENTRY, (2, "foil", "EX", None))
    with pytest.raises(sqlite3.IntegrityError):  # same printing/finish/condition/place
        conn.execute(ENTRY, (1, "foil", "EX", None))
    conn.execute(ENTRY, (1, "nonfoil", "EX", None))  # a different finish is a new row
    for bad in [(0, "foil", "NM", None), (1, "shiny", "NM", None), (1, "foil", "MP", None)]:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(ENTRY, bad)
    with pytest.raises(sqlite3.IntegrityError):  # unknown location
        conn.execute(ENTRY, (1, "foil", "NM", 99))


def test_location_in_use_cannot_be_deleted(conn):
    conn.execute(
        "INSERT INTO locations (name, kind, created_at) VALUES ('Binder 1', 'binder', 'x')"
    )
    with pytest.raises(sqlite3.IntegrityError):  # names are unique, ignoring case
        conn.execute("INSERT INTO locations (name, created_at) VALUES ('BINDER 1', 'x')")
    conn.execute(ENTRY, (1, "nonfoil", "NM", 1))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM locations WHERE id = 1")
