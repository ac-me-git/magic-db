"""Collection reads and writes (SPEC §5). All collection SQL lives here.

Every write runs in one transaction and appends exactly one ``history`` row. Problems
the user can cause raise ``CollectionError``, whose ``code`` the pages translate.
"""

import json
import sqlite3
from dataclasses import dataclass

from magicdb import db
from magicdb.search.compiler import LIKE, _contains

FINISHES = ("nonfoil", "foil", "etched")
CONDITIONS = ("MT", "NM", "EX", "GD", "LP", "PL", "PO")  # Cardmarket scale, best first
LANGUAGES = ("en", "it", "fr", "de", "es", "pt", "ja", "ko", "ru", "zhs", "zht", "ph")
KINDS = ("binder", "box", "deck", "other")
SORTS = ("added", "name", "set", "quantity", "value", "location")
PER_PAGE = 100
MAX_QUANTITY = 9999
MAX_NAME = 80

ALL = "all"  # list_entries: every location
UNASSIGNED = "none"  # list_entries: entries without a location

_KEY = ("finish", "condition", "language", "location_id")
_EDITABLE = (*_KEY, "quantity", "notes", "purchase_price")


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
    unusual_finish: bool  # this printing is not known to exist in that finish


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
    if "condition" in clean and clean["condition"] not in CONDITIONS:
        raise CollectionError("bad_condition")
    if "language" in clean and clean["language"] not in LANGUAGES:
        raise CollectionError("bad_language")
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


def _check_location(conn: sqlite3.Connection, location_id: int | None) -> str | None:
    """The location's name; None for 'unassigned'."""
    if location_id is None:
        return None
    row = conn.execute("SELECT name FROM locations WHERE id = ?", (location_id,)).fetchone()
    if row is None:
        raise CollectionError("unknown_location")
    return row["name"]


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
    conn: sqlite3.Connection, scryfall_id: str, key: dict[str, object], except_id: int = 0
) -> sqlite3.Row | None:
    """The row that already holds this printing with the same finish/condition/language/place."""
    return conn.execute(
        "SELECT * FROM entries WHERE scryfall_id = ? AND finish = ? AND condition = ? "
        "AND language = ? AND COALESCE(location_id, 0) = COALESCE(?, 0) AND id != ?",
        (
            scryfall_id,
            key["finish"],
            key["condition"],
            key["language"],
            key["location_id"],
            except_id,
        ),
    ).fetchone()


def _place(conn: sqlite3.Connection, card: dict[str, object], fields: dict[str, object]) -> tuple:
    """Put copies where they belong: on the matching row if there is one, else a new row.

    ``card`` has scryfall_id, oracle_id, name, set_code, collector_number; ``fields`` has
    quantity, finish, condition, language, location_id, notes, purchase_price.
    Returns ``(entry_id, merged)``.
    """
    now = db.utcnow()
    added = fields.get("added_at") or now  # imports and moves carry the original date
    twin = _twin(conn, card["scryfall_id"], fields)
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
        "quantity, finish, condition, language, location_id, notes, purchase_price, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            card["scryfall_id"],
            card["oracle_id"],
            card["name"],
            card["set_code"],
            card["collector_number"],
            fields["quantity"],
            fields["finish"],
            fields["condition"],
            fields["language"],
            fields["location_id"],
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


def _relocate(
    conn: sqlite3.Connection, entry: sqlite3.Row, quantity: int, location_id: int | None
) -> tuple:
    """Move copies of a row to another location. Returns ``(entry_id, merged)``.

    A whole row with nothing to merge into simply changes place: it keeps its id and
    the date it was added.
    """
    fields = {
        **_fields_of(entry),
        "quantity": quantity,
        "location_id": location_id,
        "added_at": entry["created_at"],  # moved copies keep the date they were added
    }
    whole = quantity == entry["quantity"]
    if whole and _twin(conn, entry["scryfall_id"], fields, except_id=entry["id"]) is None:
        conn.execute(
            "UPDATE entries SET location_id = ?, updated_at = ? WHERE id = ?",
            (location_id, db.utcnow(), entry["id"]),
        )
        return entry["id"], False
    _take(conn, entry, quantity)
    return _place(conn, _card_of(entry), fields)


# --- entries --------------------------------------------------------------------------


def add_entry(
    conn: sqlite3.Connection,
    scryfall_id: str,
    quantity: object = 1,
    *,
    finish: str = "nonfoil",
    condition: str = "NM",
    language: str = "en",
    location_id: int | None = None,
    notes: str | None = None,
    purchase_price: object = None,
) -> Added:
    """Add copies of a printing. Copies identical to an existing row join that row."""
    fields = _clean(
        {
            "quantity": quantity,
            "finish": finish,
            "condition": condition,
            "language": language,
            "location_id": location_id,
            "notes": notes,
            "purchase_price": purchase_price,
        }
    )
    with conn:
        printing = _printing(conn, scryfall_id)
        location = _check_location(conn, location_id)
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
            location=location,
            merged=merged,
            **{k: fields[k] for k in ("quantity", "finish", "condition", "language")},
        )
    return Added(entry_id, merged, f"|{fields['finish']}|" not in printing["finishes"])


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
        if "location_id" in changes:
            _check_location(conn, after["location_id"])
        twin = None
        if any(after[key] != before[key] for key in _KEY):
            twin = _twin(conn, entry["scryfall_id"], after, except_id=entry_id)
        if twin is not None:
            conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
            result, _ = _place(conn, _card_of(entry), after)
        else:
            result = entry_id
            conn.execute(
                "UPDATE entries SET quantity = ?, finish = ?, condition = ?, language = ?, "
                "location_id = ?, notes = ?, purchase_price = ?, updated_at = ? WHERE id = ?",
                (
                    after["quantity"],
                    after["finish"],
                    after["condition"],
                    after["language"],
                    after["location_id"],
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


def move_entry(
    conn: sqlite3.Connection, entry_id: int, quantity: object, location_id: int | None
) -> int:
    """Move some or all copies of an entry to another location.

    Returns the id of the row at the destination. Moving to where the copies already
    are does nothing.
    """
    quantity = _quantity(quantity)
    with conn:
        entry = _entry(conn, entry_id)
        if quantity > entry["quantity"]:
            raise CollectionError("too_many", have=entry["quantity"])
        destination = _check_location(conn, location_id)
        if (location_id or 0) == (entry["location_id"] or 0):
            return entry_id
        origin = _check_location(conn, entry["location_id"])
        result, merged = _relocate(conn, entry, quantity, location_id)
        _log(
            conn,
            "move",
            entry_id=entry_id,
            to_entry_id=result,
            name=entry["name"],
            quantity=quantity,
            origin=origin,
            destination=destination,
            merged=merged,
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
    return conn.execute(
        "SELECT e.*, l.name AS location_name FROM entries e "
        "LEFT JOIN locations l ON l.id = e.location_id WHERE e.id = ?",
        (entry_id,),
    ).fetchone()


def import_entries(
    conn: sqlite3.Connection,
    items: list[dict],
    *,
    replace: bool = False,
    locations: list[dict] | None = None,
    source: str = "",
) -> dict[str, object]:
    """Add many entries at once, in one transaction with one history row.

    Each item has ``card`` (scryfall_id, oracle_id, name, set_code, collector_number)
    and the entry fields, with ``location`` and ``location_kind`` as names instead of an
    id, and optionally ``added_at``. Locations are created as needed; ``locations``
    lists ones to create first, in order, even if nothing is put in them. With
    ``replace`` the collection is emptied first (locations are kept).
    """
    created: list[str] = []
    with conn:
        if replace:
            conn.execute("DELETE FROM entries")
        known = {
            row["name"].lower(): row["id"] for row in conn.execute("SELECT id, name FROM locations")
        }

        def place(name: object, kind: object, notes: object = None) -> int | None:
            if not str(name or "").strip():
                return None
            name, kind, notes = _location_fields(name, kind if kind in KINDS else "other", notes)
            if name.lower() not in known:
                position = conn.execute(
                    "SELECT IFNULL(MAX(sort_order), 0) + 1 FROM locations"
                ).fetchone()[0]
                cursor = conn.execute(
                    "INSERT INTO locations (name, kind, notes, sort_order, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (name, kind, notes, position, db.utcnow()),
                )
                known[name.lower()] = cursor.lastrowid
                created.append(name)
            return known[name.lower()]

        for location in locations or ():
            place(location.get("name"), location.get("kind"), location.get("notes"))
        copies = 0
        for item in items:
            fields = _clean(
                {key: item.get(key) for key in ("quantity", "notes", "purchase_price")}
                | {key: item[key] for key in ("finish", "condition", "language")}
            )
            fields["location_id"] = place(item.get("location"), item.get("location_kind"))
            fields["added_at"] = item.get("added_at") or None
            _place(conn, item["card"], fields)
            copies += fields["quantity"]
        result = {
            "source": source,
            "replace": replace,
            "entries": len(items),
            "copies": copies,
            "new_locations": created,
        }
        _log(conn, "import", **result)
    return result


# --- locations ------------------------------------------------------------------------


def _location_fields(name: object, kind: object, notes: object) -> tuple[str, str, str | None]:
    # a double quote cannot be written in a search (loc:"…"), so names never hold one
    name = " ".join(str(name or "").replace('"', "'").split())
    if not name or len(name) > MAX_NAME:
        raise CollectionError("bad_location_name")
    if kind not in KINDS:
        raise CollectionError("bad_kind")
    return name, str(kind), (str(notes or "").strip())[:500] or None


def create_location(
    conn: sqlite3.Connection, name: object, kind: object = "other", notes: object = None
) -> int:
    name, kind, notes = _location_fields(name, kind, notes)
    with conn:
        position = conn.execute("SELECT IFNULL(MAX(sort_order), 0) + 1 FROM locations").fetchone()
        try:
            cursor = conn.execute(
                "INSERT INTO locations (name, kind, notes, sort_order, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, kind, notes, position[0], db.utcnow()),
            )
        except sqlite3.IntegrityError:
            raise CollectionError("location_exists", name=name) from None
        _log(conn, "location", op="create", location_id=cursor.lastrowid, name=name, kind=kind)
    return cursor.lastrowid


def update_location(
    conn: sqlite3.Connection, location_id: int, name: object, kind: object, notes: object = None
) -> None:
    name, kind, notes = _location_fields(name, kind, notes)
    with conn:
        old = _check_location(conn, location_id)
        try:
            conn.execute(
                "UPDATE locations SET name = ?, kind = ?, notes = ? WHERE id = ?",
                (name, kind, notes, location_id),
            )
        except sqlite3.IntegrityError:
            raise CollectionError("location_exists", name=name) from None
        _log(conn, "location", op="update", location_id=location_id, old=old, name=name, kind=kind)


def shift_location(conn: sqlite3.Connection, location_id: int, offset: int) -> None:
    """Move a location up (-1) or down (+1) in the owner's order."""
    with conn:
        name = _check_location(conn, location_id)
        order = [row[0] for row in conn.execute("SELECT id FROM locations ORDER BY sort_order, id")]
        index = order.index(location_id)
        target = index + offset
        if not 0 <= target < len(order):
            return
        order[index], order[target] = order[target], order[index]
        conn.executemany(
            "UPDATE locations SET sort_order = ? WHERE id = ?",
            [(position, each) for position, each in enumerate(order, start=1)],
        )
        _log(
            conn, "location", op="reorder", location_id=location_id, name=name, position=target + 1
        )


def empty_location(conn: sqlite3.Connection, location_id: int, destination_id: int | None) -> int:
    """Move everything out of a location. Returns how many copies moved."""
    with conn:
        origin = _check_location(conn, location_id)
        destination = _check_location(conn, destination_id)
        if destination_id == location_id:
            return 0
        entries = conn.execute(
            "SELECT * FROM entries WHERE location_id = ? ORDER BY id", (location_id,)
        ).fetchall()
        for entry in entries:
            _relocate(conn, entry, entry["quantity"], destination_id)
        copies = sum(entry["quantity"] for entry in entries)
        if entries:
            _log(
                conn,
                "location",
                op="empty",
                location_id=location_id,
                name=origin,
                destination=destination,
                entries=len(entries),
                copies=copies,
            )
    return copies


def delete_location(conn: sqlite3.Connection, location_id: int) -> None:
    """Delete an empty location. One that still holds cards is refused."""
    with conn:
        name = _check_location(conn, location_id)
        used = conn.execute(
            "SELECT COUNT(*) FROM entries WHERE location_id = ?", (location_id,)
        ).fetchone()[0]
        if used:
            raise CollectionError("location_in_use", name=name, entries=used)
        conn.execute("DELETE FROM locations WHERE id = ?", (location_id,))
        _log(conn, "location", op="delete", location_id=location_id, name=name)


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


def list_locations(conn: sqlite3.Connection, currency: str = "eur") -> list[sqlite3.Row]:
    """Locations in the owner's order, with how much each holds."""
    join, price = _catalog_join(conn, currency)
    return conn.execute(
        f"SELECT l.id, l.name, l.kind, l.notes, COUNT(e.id) AS entries, "
        f"IFNULL(SUM(e.quantity), 0) AS copies, SUM(e.quantity * {price}) AS value "
        f"FROM locations l LEFT JOIN entries e ON e.location_id = l.id {join} "
        f"GROUP BY l.id ORDER BY l.sort_order, l.id"
    ).fetchall()


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


_ORDER_BY = {
    "added": "e.created_at DESC, e.id DESC",
    "name": "e.name COLLATE NOCASE, e.set_code, e.id",
    "set": "e.set_code, CAST(e.collector_number AS INTEGER), e.collector_number, e.id",
    "quantity": "e.quantity DESC, e.name COLLATE NOCASE, e.id",
    "value": "value IS NULL, value DESC, e.name COLLATE NOCASE, e.id",
    "location": "l.sort_order IS NULL, l.sort_order, e.name COLLATE NOCASE, e.id",
}


def list_entries(
    conn: sqlite3.Connection,
    *,
    location: str | int = ALL,
    text: str = "",
    sort: str = "added",
    page: int = 1,
    per_page: int | None = None,
    currency: str = "eur",
) -> tuple[list[sqlite3.Row], sqlite3.Row]:
    """One page of entries and the totals of everything that matches."""
    clauses, params = ["1"], []
    if location == UNASSIGNED:
        clauses.append("e.location_id IS NULL")
    elif location != ALL:
        clauses.append("e.location_id = ?")
        params.append(int(location))
    if text.strip():
        clauses.append(f"e.name {LIKE}")
        params.append(_contains(text.strip()))
    where = " AND ".join(clauses)
    join, price = _catalog_join(conn, currency)
    per_page = per_page or PER_PAGE
    rows = conn.execute(
        f"SELECT e.*, l.name AS location_name, {price} AS price, "
        f"e.quantity * {price} AS value FROM entries e "
        f"LEFT JOIN locations l ON l.id = e.location_id {join} "
        f"WHERE {where} ORDER BY {_ORDER_BY.get(sort, _ORDER_BY['added'])} LIMIT ? OFFSET ?",
        (*params, per_page, (max(1, page) - 1) * per_page),
    ).fetchall()
    return rows, totals(conn, currency, where, tuple(params))


def copies_of_card(conn: sqlite3.Connection, oracle_id: str) -> list[sqlite3.Row]:
    """Every entry of a card, whatever the printing."""
    return conn.execute(
        "SELECT e.*, l.name AS location_name FROM entries e "
        "LEFT JOIN locations l ON l.id = e.location_id WHERE e.oracle_id = ? "
        "ORDER BY e.set_code, e.collector_number, l.sort_order, e.id",
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


def copies_like(
    conn: sqlite3.Connection,
    ids: list[str],
    *,
    condition: str,
    language: str,
    location_id: int | None,
) -> dict[tuple[str, str], int]:
    """Copies of each printing, by (printing id, finish), that are in this condition,
    language and place: what fast input would add to, or take from."""
    if not ids:
        return {}
    marks = ", ".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT scryfall_id, finish, SUM(quantity) FROM entries "
        f"WHERE scryfall_id IN ({marks}) AND condition = ? AND language = ? "
        f"AND COALESCE(location_id, 0) = COALESCE(?, 0) GROUP BY scryfall_id, finish",
        [*ids, condition, language, location_id],
    )
    return {(row[0], row[1]): row[2] for row in rows}


def take_one(
    conn: sqlite3.Connection,
    scryfall_id: str,
    *,
    finish: str,
    condition: str,
    language: str,
    location_id: int | None,
) -> bool:
    """Remove one copy of this printing with exactly these traits; False if there is none."""
    key = {
        "finish": finish,
        "condition": condition,
        "language": language,
        "location_id": location_id,
    }
    twin = _twin(conn, scryfall_id, key)
    if twin is None:
        return False
    remove_entry(conn, twin["id"], 1)
    return True
