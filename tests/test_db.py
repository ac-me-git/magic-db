"""Collection database: migrations and connections (SPEC §5)."""

import sqlite3
from contextlib import closing

import pytest

from magicdb import db

ENTRY = (
    "INSERT INTO entries (scryfall_id, oracle_id, name, set_code, collector_number, quantity,"
    " finish, created_at, updated_at)"
    " VALUES ('p1', 'o1', 'Lightning Bolt', 'lea', '161', ?, ?, 'now', 'now')"
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
        assert {"entries", "history", "job_runs", "settings", "decks", "deck_cards"} <= tables
        assert "locations" not in tables
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
    conn.execute(ENTRY, (2, "foil"))
    with pytest.raises(sqlite3.IntegrityError):  # same printing and finish
        conn.execute(ENTRY, (1, "foil"))
    conn.execute(ENTRY, (1, "nonfoil"))  # a different finish is a new row
    for bad in [(0, "foil"), (1, "shiny")]:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(ENTRY, bad)


# --- migration 4: no locations, conditions, languages, formats or sideboards ----------


def schema_3(settings):
    """A collection database as version 1.3 left it, with copies that only the removed
    traits told apart, and a deck with a format and a sideboard."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(settings.collection_path)) as conn:
        for number in (1, 2, 3):
            path = next(db.MIGRATIONS_DIR.glob(f"{number:04d}_*.sql"))
            conn.executescript(path.read_text(encoding="utf-8"))
        conn.executescript(
            """
            INSERT INTO locations (id, name, kind, created_at) VALUES
              (1, 'Binder', 'binder', 'x'), (2, 'Box', 'box', 'x');
            INSERT INTO entries (id, scryfall_id, oracle_id, name, set_code, collector_number,
                                 quantity, finish, condition, language, location_id, notes,
                                 purchase_price, created_at, updated_at) VALUES
              (5, 'p1', 'o1', 'Bolt', 'm10', '146', 2, 'nonfoil', 'NM', 'en', 1, 'signed',
               NULL, '2024-02-01', '2024-02-01'),
              (3, 'p1', 'o1', 'Bolt', 'm10', '146', 3, 'nonfoil', 'EX', 'it', 2, NULL,
               1.5, '2023-01-01', '2025-01-01'),
              (7, 'p1', 'o1', 'Bolt', 'm10', '146', 1, 'foil', 'NM', 'en', NULL, NULL,
               NULL, '2024-03-01', '2024-03-01'),
              (9, 'p2', 'o2', 'Ring', 'c21', '263', 4, 'nonfoil', 'NM', 'en', 1, '',
               NULL, '2024-04-01', '2024-04-01');
            INSERT INTO decks (id, name, format, notes, created_at, updated_at) VALUES
              (1, 'Dragons', 'Commander', 'fun', 'x', 'y');
            INSERT INTO deck_cards (deck_id, oracle_id, name, quantity, board) VALUES
              (1, 'o1', 'Bolt', 3, 'main'), (1, 'o1', 'Bolt', 2, 'side'),
              (1, 'o2', 'Ring', 1, 'side');
            PRAGMA user_version = 3;
            """
        )


def test_migration_4_merges_copies_and_keeps_what_matters(settings):
    schema_3(settings)
    assert db.migrate(settings) >= 4
    with closing(db.connect(settings)) as conn:
        rows = conn.execute(
            "SELECT id, scryfall_id, quantity, finish, notes, purchase_price, created_at, "
            "updated_at FROM entries ORDER BY id"
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            # two rows of the same printing and finish: added together, lowest id, earliest
            # "added on", the note and the price kept
            (3, "p1", 5, "nonfoil", "signed", 1.5, "2023-01-01", "2025-01-01"),
            (7, "p1", 1, "foil", None, None, "2024-03-01", "2024-03-01"),
            (9, "p2", 4, "nonfoil", None, None, "2024-04-01", "2024-04-01"),
        ]
        columns = {row[1] for row in conn.execute("PRAGMA table_info(entries)")}
        assert not columns & {"condition", "language", "location_id"}
        deck = conn.execute("SELECT * FROM decks").fetchone()
        assert (deck["name"], deck["notes"]) == ("Dragons", "fun")
        assert "format" not in deck.keys()
        cards = conn.execute("SELECT oracle_id, quantity FROM deck_cards ORDER BY oracle_id")
        assert [tuple(card) for card in cards] == [("o1", 5), ("o2", 1)]  # sideboard joined
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
        assert "locations" not in tables
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        conn.execute("DELETE FROM decks WHERE id = 1")  # its list goes with it
        assert conn.execute("SELECT COUNT(*) FROM deck_cards").fetchone()[0] == 0
    backups = list(settings.backup_dir.glob("pre-migration-*.db"))
    assert len(backups) == 1  # the old file was copied first
    with closing(sqlite3.connect(backups[0])) as old:
        assert old.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 2
