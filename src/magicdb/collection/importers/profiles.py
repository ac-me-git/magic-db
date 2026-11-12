"""File formats the import understands (SPEC §8).

A profile turns a table (header + rows) into ``Row`` objects with plain text fields; it
also says which condition scale its source uses, because ``LP`` means different things
in the US scale and in Cardmarket's.

``native`` is this app's own export. ``topdecked`` was written from a real export.
``moxfield`` and ``manabox`` follow those tools' documented column names.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_MONEY_START = re.compile(r"[€$£]\s?\d+")
_CENTS = re.compile(r"\d{1,2}")

TEXT_FIELDS = (
    "name",
    "set_code",
    "set_name",
    "collector_number",
    "scryfall_id",
    "quantity",
    "finish",
    "condition",
    "language",
    "location",
    "location_kind",
    "notes",
    "purchase_price",
    "added_at",
)


@dataclass
class Row:
    line: int  # line in the file, counting the header as 1
    raw: list[str]
    name: str = ""
    set_code: str = ""
    set_name: str = ""
    collector_number: str = ""
    scryfall_id: str = ""
    quantity: str = "1"
    finish: str = ""
    condition: str = ""
    language: str = ""
    location: str = ""
    location_kind: str = ""
    notes: str = ""
    purchase_price: str = ""
    added_at: str = ""
    # filled in by resolve()
    status: str = ""  # matched | ambiguous | failed
    problem: str = ""  # a code, translated by the pages
    detail: dict = field(default_factory=dict)
    guessed: bool = False  # matched, but one of several printings had to be picked
    card: dict | None = None
    fields: dict | None = None


@dataclass(frozen=True)
class Profile:
    name: str
    scale: str  # "cardmarket" | "us"
    detect: Callable[[list[str]], bool]
    parse: Callable[[list[str], list[list[str]]], list[Row]]


def _keys(header: list[str]) -> list[str]:
    return [" ".join(cell.lower().replace("_", " ").split()) for cell in header]


def by_columns(columns: dict[str, tuple[str, ...]]) -> Callable:
    """A parser that finds each field under any of the given column names."""

    def parse(header: list[str], rows: list[list[str]]) -> list[Row]:
        keys = _keys(header)
        where = {
            name: next((keys.index(alias) for alias in aliases if alias in keys), None)
            for name, aliases in columns.items()
        }
        parsed = []
        for number, raw in enumerate(rows, start=2):
            row = Row(line=number, raw=raw)
            for name, index in where.items():
                if index is not None and index < len(raw) and raw[index].strip():
                    setattr(row, name, raw[index].strip())
            parsed.append(row)
        return parsed

    return parse


def has(*wanted: str) -> Callable[[list[str]], bool]:
    return lambda header: set(wanted) <= set(_keys(header))


# --- native: this app's own CSV export, and any simple sheet with the same columns ----

NATIVE_COLUMNS = {
    "name": ("name", "card name", "card"),
    "set_code": ("set code", "set", "edition", "setcode"),
    "set_name": ("set name", "setname"),
    "collector_number": ("collector number", "number", "cn"),
    "scryfall_id": ("scryfall id",),
    "quantity": ("quantity", "qty", "count"),
    "finish": ("finish", "foil"),
    "condition": ("condition",),
    "language": ("language", "lang"),
    "location": ("location", "binder name"),
    "location_kind": ("location kind", "binder type"),
    "notes": ("notes",),
    "purchase_price": ("purchase price",),
    "added_at": ("added",),
}

# --- Moxfield collection export -------------------------------------------------------

MOXFIELD_COLUMNS = {
    "name": ("name",),
    "set_code": ("edition",),
    "collector_number": ("collector number",),
    "quantity": ("count",),
    "finish": ("foil",),
    "condition": ("condition",),
    "language": ("language",),
    "purchase_price": ("purchase price",),
    "added_at": ("last modified",),
}

# --- ManaBox export -------------------------------------------------------------------

MANABOX_COLUMNS = {
    "name": ("name",),
    "set_code": ("set code",),
    "set_name": ("set name",),
    "collector_number": ("collector number",),
    "scryfall_id": ("scryfall id",),
    "quantity": ("quantity",),
    "finish": ("foil",),
    "condition": ("condition",),
    "language": ("language",),
    "location": ("binder name",),
    "location_kind": ("binder type",),
    "purchase_price": ("purchase price",),
}

# --- TopDecked ------------------------------------------------------------------------
#
# Header:  QUANTITY,"NAME",SETCODE,"SETNAME","COLLECTOR NUMBER",FINISH,PRICE,RARITY,ID,
#          ACQUIRED DATE,ACQUIRED PRICE,LANG,PRICE SALE,SIGNING,ALTERATION,CONDITION,NOTES,TAGS
# Row:     1,"+2 Mace",afr,"Adventures in the Forgotten Realms",1,nonfoil,€0,15,common,
#          e882c9f9-…,2025-02-11T15:31:46.501Z
#
# Two quirks, both seen in every row of a real 9,385-row export:
#   * prices use a decimal comma without quotes, so "€0,15" arrives as two fields and
#     everything after it sits one column to the right of its header;
#   * empty trailing columns are simply left out.
# So the header is not trusted for positions. The first six fields are fixed, the
# Scryfall id is found by its shape, and what follows it is read in header order.

_TOPDECKED_TAIL = (
    "added_at",  # ACQUIRED DATE
    "purchase_price",  # ACQUIRED PRICE
    "language",  # LANG
    None,  # PRICE SALE
    None,  # SIGNING
    None,  # ALTERATION
    "condition",  # CONDITION
    "notes",  # NOTES
    None,  # TAGS
)


def rejoin_prices(cells: list[str]) -> list[str]:
    """['€0', '15', 'x'] -> ['€0.15', 'x']: undo the split of an unquoted decimal comma."""
    joined: list[str] = []
    index = 0
    while index < len(cells):
        cell = cells[index]
        following = cells[index + 1] if index + 1 < len(cells) else ""
        if _MONEY_START.fullmatch(cell.strip()) and _CENTS.fullmatch(following.strip()):
            joined.append(f"{cell.strip()}.{following.strip()}")
            index += 2
        else:
            joined.append(cell)
            index += 1
    return joined


def parse_topdecked(header: list[str], rows: list[list[str]]) -> list[Row]:
    parsed = []
    for number, raw in enumerate(rows, start=2):
        row = Row(line=number, raw=raw)
        cells = [cell.strip() for cell in raw]
        for name, value in zip(
            ("quantity", "name", "set_code", "set_name", "collector_number", "finish"),
            cells,
            strict=False,
        ):
            setattr(row, name, value)
        at = next((i for i, cell in enumerate(cells) if i >= 6 and UUID.fullmatch(cell)), None)
        if at is not None:
            row.scryfall_id = cells[at]
            for name, value in zip(_TOPDECKED_TAIL, rejoin_prices(cells[at + 1 :]), strict=False):
                if name and value:
                    setattr(row, name, value)
        parsed.append(row)
    return parsed


PROFILES = (
    Profile("manabox", "us", has("manabox id"), by_columns(MANABOX_COLUMNS)),
    Profile(
        "native", "cardmarket", has("scryfall id", "location kind"), by_columns(NATIVE_COLUMNS)
    ),
    Profile("topdecked", "us", has("setcode", "acquired date"), parse_topdecked),
    Profile("moxfield", "us", has("count", "edition"), by_columns(MOXFIELD_COLUMNS)),
    # last resort: any sheet with a name column is read with the native column names
    Profile("native", "cardmarket", has("name"), by_columns(NATIVE_COLUMNS)),
)


def detect(header: list[str]) -> Profile | None:
    return next((profile for profile in PROFILES if profile.detect(header)), None)
