"""Export the collection (SPEC §8): CSV for spreadsheets and other tools, JSON as a
complete, re-importable dump."""

import csv
import io
import json
import sqlite3

from magicdb import db
from magicdb.decks import service as decks

FORMAT_VERSION = 2  # 2: no locations, conditions or languages
CSV_COLUMNS = (
    "Name",
    "Set code",
    "Set name",
    "Collector number",
    "Scryfall ID",
    "Quantity",
    "Finish",
    "Notes",
    "Purchase price",
    "Added",
)


def _entries(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every entry, by name."""
    if db.has_catalog(conn):
        set_name, join = "s.name", "LEFT JOIN cat.sets s ON s.code = e.set_code"
    else:
        set_name, join = "NULL", ""
    return conn.execute(
        f"SELECT e.*, {set_name} AS set_name FROM entries e {join} "
        f"ORDER BY e.name COLLATE NOCASE, e.set_code, e.collector_number, e.id"
    ).fetchall()


def export_csv(conn: sqlite3.Connection) -> str:
    """One row per entry, with the card spelled out so the file stands on its own."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for e in _entries(conn):
        writer.writerow(
            [
                e["name"],
                e["set_code"],
                e["set_name"] or "",
                e["collector_number"],
                e["scryfall_id"],
                e["quantity"],
                e["finish"],
                e["notes"] or "",
                "" if e["purchase_price"] is None else e["purchase_price"],
                e["created_at"],
            ]
        )
    return out.getvalue()


def export_json(conn: sqlite3.Connection) -> str:
    """Every entry and every deck."""
    dump = {
        "magic_db_export": FORMAT_VERSION,
        "exported_at": db.utcnow(),
        "schema_version": conn.execute("PRAGMA user_version").fetchone()[0],
        "decks": decks.dump(conn),
        "entries": [
            {
                "name": e["name"],
                "set_code": e["set_code"],
                "collector_number": e["collector_number"],
                "scryfall_id": e["scryfall_id"],
                "quantity": e["quantity"],
                "finish": e["finish"],
                "notes": e["notes"],
                "purchase_price": e["purchase_price"],
                "added_at": e["created_at"],
            }
            for e in _entries(conn)
        ],
    }
    return json.dumps(dump, ensure_ascii=False, indent=1) + "\n"
