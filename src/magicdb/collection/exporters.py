"""Export the collection (SPEC §8): CSV for spreadsheets and other tools, JSON as a
complete, re-importable dump."""

import csv
import io
import json
import sqlite3

from magicdb import db
from magicdb.decks import service as decks

FORMAT_VERSION = 1
CSV_COLUMNS = (
    "Name",
    "Set code",
    "Set name",
    "Collector number",
    "Scryfall ID",
    "Quantity",
    "Finish",
    "Condition",
    "Language",
    "Location",
    "Location kind",
    "Notes",
    "Purchase price",
    "Added",
)


def _entries(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every entry, location by location in the owner's order, then by name."""
    if db.has_catalog(conn):
        set_name, join = "s.name", "LEFT JOIN cat.sets s ON s.code = e.set_code"
    else:
        set_name, join = "NULL", ""
    return conn.execute(
        f"SELECT e.*, l.name AS location_name, l.kind AS location_kind, {set_name} AS set_name "
        f"FROM entries e LEFT JOIN locations l ON l.id = e.location_id {join} "
        f"ORDER BY l.sort_order IS NULL, l.sort_order, l.id, e.name COLLATE NOCASE, "
        f"e.set_code, e.collector_number, e.id"
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
                e["condition"],
                e["language"],
                e["location_name"] or "",
                e["location_kind"] or "",
                e["notes"] or "",
                "" if e["purchase_price"] is None else e["purchase_price"],
                e["created_at"],
            ]
        )
    return out.getvalue()


def export_json(conn: sqlite3.Connection) -> str:
    """Locations (also the empty ones, with their notes and order) and every entry."""
    locations = conn.execute(
        "SELECT name, kind, notes FROM locations ORDER BY sort_order, id"
    ).fetchall()
    dump = {
        "magic_db_export": FORMAT_VERSION,
        "exported_at": db.utcnow(),
        "schema_version": conn.execute("PRAGMA user_version").fetchone()[0],
        "locations": [dict(row) for row in locations],
        "decks": decks.dump(conn),
        "entries": [
            {
                "name": e["name"],
                "set_code": e["set_code"],
                "collector_number": e["collector_number"],
                "scryfall_id": e["scryfall_id"],
                "quantity": e["quantity"],
                "finish": e["finish"],
                "condition": e["condition"],
                "language": e["language"],
                "location": e["location_name"],
                "notes": e["notes"],
                "purchase_price": e["purchase_price"],
                "added_at": e["created_at"],
            }
            for e in _entries(conn)
        ],
    }
    return json.dumps(dump, ensure_ascii=False, indent=1) + "\n"
