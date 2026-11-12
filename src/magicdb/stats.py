"""Collection statistics (SPEC §9).

One query reads every entry of the chosen scope with the catalog columns it needs; the
totals and breakdowns are then counted in Python, which keeps each of them a few lines
and easy to test. Set completion is the one thing done in SQL.
"""

import sqlite3
from collections import defaultdict
from dataclasses import dataclass

from magicdb.catalog.ingest import COLOR_BITS, RARITY_ORD, SUPERTYPES
from magicdb.collection import service

TOP = 20
COLOR_ORDER = ("w", "u", "b", "r", "g", "m", "c")
TYPE_ORDER = (
    "Creature",
    "Planeswalker",
    "Battle",
    "Instant",
    "Sorcery",
    "Artifact",
    "Enchantment",
    "Land",
)
MV_BUCKETS = ("0", "1", "2", "3", "4", "5", "6", "7+")
SET_SORTS = ("percent", "name", "date")
_LETTER = {bit: letter.lower() for letter, bit in COLOR_BITS.items()}


@dataclass(frozen=True)
class Bar:
    key: str  # what is counted: "r", "mythic", "Creature", "2019", a location name…
    copies: int
    cards: int  # distinct cards (oracle ids)


@dataclass(frozen=True)
class Totals:
    entries: int
    copies: int
    printings: int
    cards: int
    value: float
    unpriced: int  # copies whose printing has no price


@dataclass(frozen=True)
class SetProgress:
    code: str
    name: str
    set_type: str
    released_at: str
    owned: int  # distinct collector numbers owned
    total: int  # distinct collector numbers in the catalog
    percent: int  # rounded down, so 100 means complete

    @property
    def complete(self) -> bool:
        return self.owned >= self.total


def scope(location: str | int) -> tuple[str, list]:
    """WHERE clause and parameters for 'everything', 'no location' or one location."""
    if location == service.UNASSIGNED:
        return "e.location_id IS NULL", []
    if location == service.ALL:
        return "1", []
    return "e.location_id = ?", [int(location)]


def collect(
    conn: sqlite3.Connection, location: str | int = service.ALL, currency: str = "eur"
) -> list[sqlite3.Row]:
    """Every entry in scope, with the card facts and the price of one copy."""
    where, params = scope(location)
    return conn.execute(
        f"SELECT e.id, e.scryfall_id, e.oracle_id, e.name, e.set_code, e.collector_number, "
        f"e.quantity, e.finish, e.condition, e.language, e.created_at, "
        f"l.name AS location_name, l.sort_order AS location_order, "
        f"p.colors, p.rarity, p.type_line, p.cmc, p.year, {service.price_sql(currency)} AS price "
        f"FROM entries e LEFT JOIN locations l ON l.id = e.location_id "
        f"LEFT JOIN cat.printings p ON p.id = e.scryfall_id WHERE {where}",
        params,
    ).fetchall()


def totals(rows: list[sqlite3.Row]) -> Totals:
    return Totals(
        entries=len(rows),
        copies=sum(row["quantity"] for row in rows),
        printings=len({row["scryfall_id"] for row in rows}),
        cards=len({row["oracle_id"] for row in rows}),
        value=sum(row["quantity"] * row["price"] for row in rows if row["price"] is not None),
        unpriced=sum(row["quantity"] for row in rows if row["price"] is None),
    )


# --- what each breakdown counts a card under ------------------------------------------


def color_of(row: sqlite3.Row) -> list[str]:
    """One of w u b r g, 'm' for two or more colours, 'c' for none."""
    mask = row["colors"]
    if mask is None:
        return []
    if mask == 0:
        return ["c"]
    return [_LETTER.get(mask, "m")]


def types_of(row: sqlite3.Row) -> list[str]:
    """Card types of the front face: an Artifact Creature counts under both."""
    front = (row["type_line"] or "").split(" // ")[0].split(" — ")[0]
    return [word for word in front.split() if word not in SUPERTYPES]


def mana_value_of(row: sqlite3.Row) -> list[str]:
    """Bucket for the mana curve. Lands are left out: they would all pile up at 0."""
    if row["cmc"] is None or "Land" in types_of(row):
        return []
    return [MV_BUCKETS[min(int(row["cmc"]), len(MV_BUCKETS) - 1)]]


def _count(rows: list[sqlite3.Row], keys_of) -> dict[str, Bar]:
    copies: dict[str, int] = defaultdict(int)
    cards: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        for key in keys_of(row):
            copies[key] += row["quantity"]
            cards[key].add(row["oracle_id"])
    return {key: Bar(key, copies[key], len(cards[key])) for key in copies}


def _ordered(bars: dict[str, Bar], order: tuple[str, ...] | list[str]) -> list[Bar]:
    """Bars in a fixed order first, then whatever else turned up, alphabetically."""
    known = [bars[key] for key in order if key in bars]
    return known + [bars[key] for key in sorted(bars) if key not in order]


def breakdowns(rows: list[sqlite3.Row]) -> dict[str, list[Bar]]:
    locations = sorted(
        {(row["location_order"], row["location_name"]) for row in rows if row["location_name"]}
    )
    by_location = _count(rows, lambda row: [row["location_name"] or ""])
    return {
        "color": _ordered(_count(rows, color_of), COLOR_ORDER),
        "rarity": _ordered(
            _count(rows, lambda row: [row["rarity"]] if row["rarity"] else []), tuple(RARITY_ORD)
        ),
        "type": _ordered(_count(rows, types_of), TYPE_ORDER),
        "mana_value": _ordered(_count(rows, mana_value_of), MV_BUCKETS),
        "year": _ordered(_count(rows, lambda row: [str(row["year"])] if row["year"] else []), ()),
        "finish": _ordered(_count(rows, lambda row: [row["finish"]]), service.FINISHES),
        "condition": _ordered(_count(rows, lambda row: [row["condition"]]), service.CONDITIONS),
        "language": _ordered(_count(rows, lambda row: [row["language"]]), service.LANGUAGES),
        # in the owner's order, cards without a location last (key "")
        "location": _ordered(by_location, [name for _, name in locations] + [""]),
    }


def most_valuable(rows: list[sqlite3.Row], limit: int = TOP) -> list[sqlite3.Row]:
    priced = [row for row in rows if row["price"] is not None]
    priced.sort(key=lambda row: (-row["quantity"] * row["price"], row["name"], row["id"]))
    return priced[:limit]


def recently_added(rows: list[sqlite3.Row], limit: int = TOP) -> list[sqlite3.Row]:
    return sorted(rows, key=lambda row: (row["created_at"], row["id"]), reverse=True)[:limit]


# --- set completion -------------------------------------------------------------------


def set_completion(
    conn: sqlite3.Connection,
    location: str | int = service.ALL,
    sort: str = "percent",
    set_type: str = "",
) -> list[SetProgress]:
    """Progress in every paper set with at least one card owned."""
    where, params = scope(location)
    # Two small queries joined here: asking SQLite to join entries, printings and sets
    # makes it walk all 120k printings (0.6 s on a 9,000-entry collection). An entry
    # carries the set code and collector number of its printing, so the catalog's
    # printings are not needed at all.
    owned = dict(
        conn.execute(
            f"SELECT e.set_code, COUNT(DISTINCT e.collector_number) FROM entries e "
            f"WHERE {where} GROUP BY e.set_code",
            params,
        )
    )
    found = conn.execute(
        "SELECT code, name, set_type, IFNULL(released_at, '') AS released_at, "
        "catalog_count AS total FROM cat.sets WHERE is_digital = 0 AND catalog_count > 0"
    ).fetchall()
    sets = [
        SetProgress(
            code=row["code"],
            name=row["name"],
            set_type=row["set_type"],
            released_at=row["released_at"],
            owned=min(owned[row["code"]], row["total"]),
            total=row["total"],
            percent=100 * min(owned[row["code"]], row["total"]) // row["total"],
        )
        for row in found
        if row["code"] in owned and (not set_type or row["set_type"] == set_type)
    ]
    if sort == "name":
        sets.sort(key=lambda s: (s.name.lower(), s.code))
    elif sort == "date":
        sets.sort(key=lambda s: (s.released_at, s.code), reverse=True)
    else:
        sets.sort(key=lambda s: (-s.owned / s.total, s.name.lower(), s.code))
    return sets


def set_types(conn: sqlite3.Connection, location: str | int = service.ALL) -> list[str]:
    """The kinds of set there is something in, for the filter."""
    return sorted({progress.set_type for progress in set_completion(conn, location)})


def missing_query(code: str, location_name: str | None = None) -> str:
    """The search that lists what is missing from a set (in one location, if given)."""
    if location_name:
        return f'set:{code} -loc:"{location_name}" unique:prints order:set'
    return f"set:{code} haveprint:no unique:prints order:set"
