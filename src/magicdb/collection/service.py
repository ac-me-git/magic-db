"""Collection reads and writes (SPEC §5). All collection SQL lives here.

Every write runs in one transaction and appends exactly one ``history`` row. Problems
the user can cause raise ``CollectionError``, whose ``code`` the pages translate.
"""

import json
import sqlite3
from dataclasses import dataclass

from magicdb import db

FINISHES = ("nonfoil", "foil", "etched")
MAX_QUANTITY = 9999

_EDITABLE = ("finish", "quantity", "notes", "purchase_price")


class CollectionError(ValueError):
    """Something the user asked for cannot be done. ``code`` names the reason."""

    def __init__(self, code: str, **params: object) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


@dataclass(frozen=True)
class Added:
    entry_id: int
    merged: bool  # the copies joined an existing row


# --- validation -----------------------------------------------------------------------


def _quantity(value: object) -> int:
    try:
        quantity = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        raise CollectionError("bad_quantity") from None
    if not 1 <= quantity <= MAX_QUANTITY:
        raise CollectionError("bad_quantity")
    return quantity


def _price(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        price = float(str(value).replace(",", "."))
    except ValueError:
        raise CollectionError("bad_price") from None
    if not 0 <= price < 10_000_000:
        raise CollectionError("bad_price")
    return price


def _clean(changes: dict[str, object]) -> dict[str, object]:
    """Validate the fields of an entry that are present in ``changes``."""
    clean = dict(changes)
    if "quantity" in clean:
        clean["quantity"] = _quantity(clean["quantity"])
    if "finish" in clean and clean["finish"] not in FINISHES:
        raise CollectionError("bad_finish")
    if "notes" in clean:
        clean["notes"] = (str(clean["notes"] or "").strip())[:500] or None
    if "purchase_price" in clean:
        clean["purchase_price"] = _price(clean["purchase_price"])
    return clean


def _entry(conn: sqlite3.Connection, entry_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        raise CollectionError("unknown_entry")
    return row


def _printing(conn: sqlite3.Connection, scryfall_id: str) -> sqlite3.Row:
    if not db.has_catalog(conn):
        raise CollectionError("no_catalog")
    row = conn.execute(
        "SELECT id, oracle_id, name, set_code, collector_number, finishes "
        "FROM cat.printings WHERE id = ?",
        (scryfall_id,),
    ).fetchone()
    if row is None:
        raise CollectionError("unknown_card")
    return row


# --- internals shared by the writes ---------------------------------------------------


def _log(conn: sqlite3.Connection, action: str, **detail: object) -> None:
    conn.execute(
        "INSERT INTO history (ts, action, detail) VALUES (?, ?, ?)",
        (db.utcnow(), action, json.dumps(detail, ensure_ascii=False)),
    )


def _merge_notes(first: str | None, second: str | None) -> str | None:
    if not second or (first and second in first.split("; ")):
        return first
    return f"{first}; {second}"[:500] if first else second


def _twin(
    conn: sqlite3.Connection, scryfall_id: str, finish: object, except_id: int = 0
) -> sqlite3.Row | None:
    """The row that already holds this printing in this finish."""
    return conn.execute(
        "SELECT * FROM entries WHERE scryfall_id = ? AND finish = ? AND id != ?",
        (scryfall_id, finish, except_id),
    ).fetchone()


def _place(conn: sqlite3.Connection, card: dict[str, object], fields: dict[str, object]) -> tuple:
    """Put copies where they belong: on the matching row if there is one, else a new row.

    ``card`` has scryfall_id, oracle_id, name, set_code, collector_number; ``fields`` has
    quantity, finish, notes, purchase_price.
    Returns ``(entry_id, merged)``.
    """
    now = db.utcnow()
    added = fields.get("added_at") or now  # imports carry the original date
    twin = _twin(conn, card["scryfall_id"], fields["finish"])
    if twin is not None:
        price = fields["purchase_price"]
        conn.execute(
            "UPDATE entries SET quantity = quantity + ?, notes = ?, purchase_price = ?, "
            "created_at = MIN(created_at, ?), updated_at = ? WHERE id = ?",
            (
                fields["quantity"],
                _merge_notes(twin["notes"], fields["notes"]),
                twin["purchase_price"] if price is None else price,
                fields.get("added_at") or twin["created_at"],
                now,
                twin["id"],
            ),
        )
        return twin["id"], True
    cursor = conn.execute(
        "INSERT INTO entries (scryfall_id, oracle_id, name, set_code, collector_number, "
        "quantity, finish, notes, purchase_price, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            card["scryfall_id"],
            card["oracle_id"],
            card["name"],
            card["set_code"],
            card["collector_number"],
            fields["quantity"],
            fields["finish"],
            fields["notes"],
            fields["purchase_price"],
            added,
            now,
        ),
    )
    return cursor.lastrowid, False


def _card_of(entry: sqlite3.Row) -> dict[str, object]:
    keys = ("scryfall_id", "oracle_id", "name", "set_code", "collector_number")
    return {key: entry[key] for key in keys}


def _fields_of(entry: sqlite3.Row) -> dict[str, object]:
    return {key: entry[key] for key in _EDITABLE}


def _take(conn: sqlite3.Connection, entry: sqlite3.Row, quantity: int) -> None:
    """Remove ``quantity`` copies from a row, deleting it when none are left."""
    if quantity > entry["quantity"]:
        raise CollectionError("too_many", have=entry["quantity"])
    if quantity == entry["quantity"]:
        conn.execute("DELETE FROM entries WHERE id = ?", (entry["id"],))
    else:
        conn.execute(
            "UPDATE entries SET quantity = quantity - ?, updated_at = ? WHERE id = ?",
            (quantity, db.utcnow(), entry["id"]),
        )


# --- entries --------------------------------------------------------------------------


def add_entry(
    conn: sqlite3.Connection,
    scryfall_id: str,
    quantity: object = 1,
    *,
    finish: str = "nonfoil",
    notes: str | None = None,
    purchase_price: object = None,
) -> Added:
    """Add copies of a printing. Copies identical to an existing row join that row."""
    fields = _clean(
        {
            "quantity": quantity,
            "finish": finish,
            "notes": notes,
            "purchase_price": purchase_price,
        }
    )
    with conn:
        printing = _printing(conn, scryfall_id)
        card = {
            "scryfall_id": printing["id"],
            "oracle_id": printing["oracle_id"],
            "name": printing["name"],
            "set_code": printing["set_code"],
            "collector_number": printing["collector_number"],
        }
        entry_id, merged = _place(conn, card, fields)
        _log(
            conn,
            "add",
            entry_id=entry_id,
            name=card["name"],
            set_code=card["set_code"],
            collector_number=card["collector_number"],
            merged=merged,
            **{k: fields[k] for k in ("quantity", "finish")},
        )
    return Added(entry_id, merged)


def update_entry(conn: sqlite3.Connection, entry_id: int, **changes: object) -> int:
    """Change an entry. If it becomes identical to another row, the two are merged.

    Returns the id of the row that now holds the copies.
    """
    unknown = set(changes) - set(_EDITABLE)
    if unknown:
        raise TypeError(f"not editable: {', '.join(sorted(unknown))}")
    changes = _clean(changes)
    with conn:
        entry = _entry(conn, entry_id)
        before = _fields_of(entry)
        after = {**before, **changes}
        if after == before:
            return entry_id
        twin = None
        if after["finish"] != before["finish"]:
            twin = _twin(conn, entry["scryfall_id"], after["finish"], except_id=entry_id)
        if twin is not None:
            conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
            result, _ = _place(conn, _card_of(entry), after)
        else:
            result = entry_id
            conn.execute(
                "UPDATE entries SET quantity = ?, finish = ?, notes = ?, purchase_price = ?, "
                "updated_at = ? WHERE id = ?",
                (
                    after["quantity"],
                    after["finish"],
                    after["notes"],
                    after["purchase_price"],
                    db.utcnow(),
                    entry_id,
                ),
            )
        _log(
            conn,
            "edit",
            entry_id=entry_id,
            name=entry["name"],
            before={k: before[k] for k in changes},
            after={k: after[k] for k in changes},
            merged_into=result if twin is not None else None,
        )
    return result


def remove_entry(conn: sqlite3.Connection, entry_id: int, quantity: object = None) -> None:
    """Remove some copies of an entry, or all of them when ``quantity`` is None."""
    with conn:
        entry = _entry(conn, entry_id)
        count = entry["quantity"] if quantity in (None, "") else _quantity(quantity)
        _take(conn, entry, count)
        _log(
            conn,
            "remove",
            entry_id=entry_id,
            name=entry["name"],
            set_code=entry["set_code"],
            quantity=count,
            left=entry["quantity"] - count,
        )


def get_entry(conn: sqlite3.Connection, entry_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()


def import_entries(
    conn: sqlite3.Connection,
    items: list[dict],
    *,
    replace: bool = False,
    source: str = "",
) -> dict[str, object]:
    """Add many entries at once, in one transaction with one history row.

    Each item has ``card`` (scryfall_id, oracle_id, name, set_code, collector_number)
    and the entry fields (quantity, finish, notes, purchase_price), and optionally
    ``added_at``. With ``replace`` the collection is emptied first.
    """
    with conn:
        if replace:
            conn.execute("DELETE FROM entries")
        copies = 0
        for item in items:
            fields = _clean(
                {key: item.get(key) for key in ("quantity", "notes", "purchase_price")}
                | {"finish": item["finish"]}
            )
            fields["added_at"] = item.get("added_at") or None
            _place(conn, item["card"], fields)
            copies += fields["quantity"]
        result = {"source": source, "replace": replace, "entries": len(items), "copies": copies}
        _log(conn, "import", **result)
    return result


# --- reads ----------------------------------------------------------------------------


def price_sql(currency: str) -> str:
    """SQL for the price of one copy of entry ``e`` given its printing ``p`` (SPEC §9)."""
    if currency == "usd":
        return (
            "CASE e.finish WHEN 'nonfoil' THEN p.price_usd "
            "WHEN 'foil' THEN COALESCE(p.price_usd_foil, p.price_usd) "
            "ELSE COALESCE(p.price_usd_etched, p.price_usd_foil, p.price_usd) END"
        )
    return (
        "CASE e.finish WHEN 'nonfoil' THEN p.price_eur "
        "ELSE COALESCE(p.price_eur_foil, p.price_eur) END"
    )


def _catalog_join(conn: sqlite3.Connection, currency: str) -> tuple[str, str]:
    """``(join clause, price expression)``; without a catalog there are no prices."""
    if db.has_catalog(conn):
        return "LEFT JOIN cat.printings p ON p.id = e.scryfall_id", price_sql(currency)
    return "", "NULL"


def totals(
    conn: sqlite3.Connection, currency: str = "eur", where: str = "1", params: tuple = ()
) -> sqlite3.Row:
    """Entries, copies and value of the collection, or of the part matching ``where``."""
    join, price = _catalog_join(conn, currency)
    return conn.execute(
        f"SELECT COUNT(e.id) AS entries, IFNULL(SUM(e.quantity), 0) AS copies, "
        f"SUM(e.quantity * {price}) AS value FROM entries e {join} WHERE {where}",
        params,
    ).fetchone()


def copies_of_card(conn: sqlite3.Connection, oracle_id: str) -> list[sqlite3.Row]:
    """Every entry of a card, whatever the printing."""
    return conn.execute(
        "SELECT * FROM entries WHERE oracle_id = ? ORDER BY set_code, collector_number, id",
        (oracle_id,),
    ).fetchall()


def owned_counts(conn: sqlite3.Connection, ids: list[str], by: str) -> dict[str, int]:
    """Copies owned for each id: printing ids when ``by`` is 'prints', else oracle ids."""
    if not ids:
        return {}
    column = "scryfall_id" if by == "prints" else "oracle_id"  # fixed names, never user text
    marks = ", ".join("?" * len(ids))
    return dict(
        conn.execute(
            f"SELECT {column}, SUM(quantity) FROM entries WHERE {column} IN ({marks}) "
            f"GROUP BY {column}",
            ids,
        )
    )


def copies_by_finish(conn: sqlite3.Connection, ids: list[str]) -> dict[tuple[str, str], int]:
    """Copies of each printing by (printing id, finish): what fast input's boxes show."""
    if not ids:
        return {}
    marks = ", ".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT scryfall_id, finish, quantity FROM entries WHERE scryfall_id IN ({marks})",
        ids,
    )
    return {(row[0], row[1]): row[2] for row in rows}


def take_one(conn: sqlite3.Connection, scryfall_id: str, *, finish: str) -> bool:
    """Remove one copy of this printing in this finish; False if there is none."""
    twin = _twin(conn, scryfall_id, finish)
    if twin is None:
        return False
    remove_entry(conn, twin["id"], 1)
    return True
