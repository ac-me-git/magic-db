"""Import a collection from a file (SPEC §8).

``parse_file`` reads a CSV (or this app's JSON dump) into rows; ``resolve`` finds the
printing each row means and validates its values, without writing anything;
``to_items`` hands the matched rows to ``service.import_entries``.
"""

import csv
import io
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime

from magicdb import db
from magicdb.collection import service
from magicdb.collection.exporters import CSV_COLUMNS
from magicdb.collection.importers.profiles import PROFILES, UUID, Profile, Row, detect
from magicdb.search.compiler import exact_name

__all__ = [
    "PROFILES",
    "Defaults",
    "FileProblem",
    "Parsed",
    "Row",
    "parse_file",
    "resolve",
    "summary",
    "to_items",
    "unresolved_csv",
]

MAX_ROWS = 200_000
DELIMITERS = (",", ";", "\t")


class FileProblem(ValueError):
    """The file as a whole cannot be imported. ``code`` names the reason."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class Parsed:
    profile: str
    scale: str
    header: list[str]
    rows: list[Row]
    locations: list[dict] = field(default_factory=list)  # JSON dumps only


@dataclass(frozen=True)
class Defaults:
    """What to use for rows that do not say."""

    condition: str = "NM"
    language: str = "en"
    location: str = ""
    location_kind: str = "other"


# --- reading the file -----------------------------------------------------------------


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _parse_json(text: str) -> Parsed:
    try:
        dump = json.loads(text)
    except json.JSONDecodeError:
        raise FileProblem("unreadable") from None
    if not isinstance(dump, dict) or "magic_db_export" not in dump:
        raise FileProblem("unknown_format")
    rows = []
    for number, entry in enumerate(dump.get("entries") or [], start=1):
        if not isinstance(entry, dict):
            continue
        row = Row(line=number, raw=[])
        for name in ("name", "set_code", "collector_number", "scryfall_id", "finish", "condition"):
            setattr(row, name, str(entry.get(name) or ""))
        row.language = str(entry.get("language") or "")
        row.quantity = str(entry.get("quantity") or "")
        row.location = str(entry.get("location") or "")
        row.notes = str(entry.get("notes") or "")
        price = entry.get("purchase_price")
        row.purchase_price = "" if price is None else str(price)
        row.added_at = str(entry.get("added_at") or "")
        row.raw = [str(getattr(row, name)) for name in _NATIVE_ORDER]
        rows.append(row)
    locations = [loc for loc in dump.get("locations") or [] if isinstance(loc, dict)]
    kinds = {str(loc.get("name") or "").lower(): loc.get("kind") for loc in locations}
    for row in rows:
        row.location_kind = str(kinds.get(row.location.lower()) or "")
    return Parsed("native", "cardmarket", list(CSV_COLUMNS), rows, locations)


_NATIVE_ORDER = (
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


def parse_file(data: bytes) -> Parsed:
    """Read an uploaded file. Raises ``FileProblem`` if it is not a format we know."""
    text = _decode(data).replace("\x00", "")
    if not text.strip():
        raise FileProblem("empty")
    if text.lstrip().startswith("{"):
        return _parse_json(text)
    first_line = text.lstrip().splitlines()[0]
    delimiter = max(DELIMITERS, key=first_line.count)
    try:
        table = [row for row in csv.reader(io.StringIO(text), delimiter=delimiter) if any(row)]
    except csv.Error:
        raise FileProblem("unreadable") from None
    if len(table) < 2:
        raise FileProblem("empty")
    if len(table) - 1 > MAX_ROWS:
        raise FileProblem("too_big")
    header, rows = table[0], table[1:]
    profile: Profile | None = detect(header)
    if profile is None:
        raise FileProblem("unknown_format")
    return Parsed(profile.name, profile.scale, header, profile.parse(header, rows))


# --- translating values ---------------------------------------------------------------


def _norm(value: str) -> str:
    return " ".join(value.lower().replace("_", " ").replace("-", " ").split())


_FINISH = {
    "nonfoil": ("", "nonfoil", "non foil", "normal", "regular", "no", "false", "0"),
    "foil": ("foil", "yes", "true", "1"),
    "etched": ("etched", "foil etched", "etched foil"),
}
_FINISHES = {alias: finish for finish, aliases in _FINISH.items() for alias in aliases}

# Cardmarket codes as they are, plus their names
_CARDMARKET = {
    "mt": "MT",
    "mint": "MT",
    "nm": "NM",
    "near mint": "NM",
    "ex": "EX",
    "excellent": "EX",
    "gd": "GD",
    "good": "GD",
    "lp": "LP",
    "light played": "LP",
    "pl": "PL",
    "played": "PL",
    "po": "PO",
    "poor": "PO",
}
# the US (TCGplayer) scale, translated as in SPEC §8
_US = {
    "m": "MT",
    "mint": "MT",
    "nm": "NM",
    "near mint": "NM",
    "lp": "EX",
    "lightly played": "EX",
    "light played": "EX",
    "sp": "EX",
    "slightly played": "EX",
    "excellent": "EX",
    "mp": "GD",
    "moderately played": "GD",
    "good": "GD",
    "hp": "PL",
    "heavily played": "PL",
    "played": "PL",
    "d": "PO",
    "dmg": "PO",
    "damaged": "PO",
    "poor": "PO",
}
_LANGUAGE = {
    "en": ("en", "english", "eng"),
    "it": ("it", "italian", "ita", "italiano"),
    "fr": ("fr", "french", "fra"),
    "de": ("de", "german", "deu", "ger"),
    "es": ("es", "spanish", "spa", "sp"),
    "pt": ("pt", "portuguese", "por"),
    "ja": ("ja", "jp", "japanese", "jpn"),
    "ko": ("ko", "kr", "korean", "kor"),
    "ru": ("ru", "russian", "rus"),
    "zhs": ("zhs", "cs", "zh cn", "chinese simplified", "simplified chinese", "zh hans"),
    "zht": ("zht", "ct", "zh tw", "chinese traditional", "traditional chinese", "zh hant"),
    "ph": ("ph", "phyrexian"),
}
_LANGUAGES = {alias: code for code, aliases in _LANGUAGE.items() for alias in aliases}


def _price(value: str) -> float | None:
    cleaned = value.strip().lstrip("€$£ ").replace(" ", "")
    if not cleaned:
        return None
    if "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    price = float(cleaned)  # ValueError is the caller's "bad_price"
    if not 0 <= price < 10_000_000:
        raise ValueError(value)
    return price


def _date(value: str) -> str | None:
    """An ISO timestamp in our own format, or None if it cannot be read."""
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def _fields(row: Row, scale: str, defaults: Defaults) -> dict:
    """The entry fields of a row; raises ``_Problem`` with the code of what is wrong."""
    if not (row.quantity.isascii() and row.quantity.isdigit()):
        raise _Problem("bad_quantity")
    quantity = int(row.quantity)
    if not 1 <= quantity <= service.MAX_QUANTITY:
        raise _Problem("bad_quantity")
    finish = _FINISHES.get(_norm(row.finish))
    if finish is None:
        raise _Problem("bad_finish", value=row.finish)
    condition = defaults.condition
    if row.condition.strip():
        condition = (_US if scale == "us" else _CARDMARKET).get(_norm(row.condition))
        if condition is None:
            raise _Problem("bad_condition", value=row.condition)
    language = defaults.language
    if row.language.strip():
        language = _LANGUAGES.get(_norm(row.language))
        if language is None:
            raise _Problem("bad_language", value=row.language)
    try:
        price = _price(row.purchase_price)
    except ValueError:
        raise _Problem("bad_price", value=row.purchase_price) from None
    location = " ".join(row.location.split())[: service.MAX_NAME]
    return {
        "quantity": quantity,
        "finish": finish,
        "condition": condition,
        "language": language,
        "location": location or defaults.location,
        "location_kind": (row.location_kind.lower() if location else defaults.location_kind),
        "notes": row.notes.strip() or None,
        "purchase_price": price,
        "added_at": _date(row.added_at) if row.added_at else None,
    }


class _Problem(Exception):
    def __init__(self, code: str, **detail: object) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


# --- finding the printing -------------------------------------------------------------

_CARD = "p.id, p.oracle_id, p.name, p.name_norm, p.set_code, p.collector_number, p.pref"


class _Catalog:
    """Printing lookups, remembered so that a big file does not repeat them."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.by_name: dict[str, list[sqlite3.Row]] = {}
        self.set_codes: dict[str, str] | None = None

    def by_id(self, scryfall_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            f"SELECT {_CARD} FROM cat.printings p WHERE p.id = ?", (scryfall_id,)
        ).fetchone()

    def by_number(self, set_code: str, number: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            f"SELECT {_CARD} FROM cat.printings p "
            f"WHERE p.set_code = ? AND LOWER(p.collector_number) = ?",
            (set_code.lower(), number.lower()),
        ).fetchall()

    def named(self, name: str) -> list[sqlite3.Row]:
        """Paper printings of the card with this name (or with a face of this name)."""
        key = name.lower()
        if key not in self.by_name:
            where, params = exact_name(name)
            self.by_name[key] = self.conn.execute(
                f"SELECT {_CARD} FROM cat.printings p WHERE {where} AND p.is_digital = 0",
                params,
            ).fetchall()
        return self.by_name[key]

    def set_code(self, set_name: str) -> str | None:
        if self.set_codes is None:
            self.set_codes = {
                row["name"].lower(): row["code"]
                for row in self.conn.execute("SELECT code, name FROM cat.sets")
            }
        return self.set_codes.get(set_name.lower())


def _same_name(printing: sqlite3.Row, name: str) -> bool:
    wanted = name.lower()
    full = printing["name"].lower()
    return wanted == full or wanted in full.split(" // ")


def _find(catalog: _Catalog, row: Row) -> tuple[sqlite3.Row, bool]:
    """The printing a row means, in the order of SPEC §8, and whether it was a guess."""
    if UUID.fullmatch(row.scryfall_id.lower()):
        printing = catalog.by_id(row.scryfall_id.lower())
        if printing is not None:
            return printing, False
    if row.set_code and row.collector_number:
        found = catalog.by_number(row.set_code, row.collector_number)
        if row.name:
            found = [p for p in found if _same_name(p, row.name)]
        if len(found) == 1:
            return found[0], False
    if not row.name:
        raise _Problem("unknown_card")
    printings = catalog.named(row.name)
    if not printings:
        raise _Problem("unknown_card")
    set_code = row.set_code.lower() or (catalog.set_code(row.set_name) if row.set_name else None)
    if set_code:
        in_set = [p for p in printings if p["set_code"] == set_code]
        if len(in_set) == 1:
            return in_set[0], False
        if in_set:  # several versions in that set and no number to tell them apart
            return max(in_set, key=lambda p: p["pref"]), True
    if len(printings) == 1:
        return printings[0], False
    raise _Problem("ambiguous", printings=len(printings))


def resolve(conn: sqlite3.Connection, parsed: Parsed, defaults: Defaults | None = None) -> None:
    """Decide for every row whether it can be imported. Writes nothing."""
    if not db.has_catalog(conn):
        raise FileProblem("no_catalog")
    defaults = defaults or Defaults()
    catalog = _Catalog(conn)
    for row in parsed.rows:
        try:
            row.fields = _fields(row, parsed.scale, defaults)
            printing, row.guessed = _find(catalog, row)
        except _Problem as problem:
            row.status = "ambiguous" if problem.code == "ambiguous" else "failed"
            row.problem, row.detail, row.card = problem.code, problem.detail, None
            continue
        row.status, row.problem, row.detail = "matched", "", {}
        row.card = {
            "scryfall_id": printing["id"],
            "oracle_id": printing["oracle_id"],
            "name": printing["name"],
            "set_code": printing["set_code"],
            "collector_number": printing["collector_number"],
        }


def summary(parsed: Parsed) -> dict[str, int]:
    matched = [row for row in parsed.rows if row.status == "matched"]
    return {
        "rows": len(parsed.rows),
        "matched": len(matched),
        "guessed": sum(row.guessed for row in matched),
        "ambiguous": sum(row.status == "ambiguous" for row in parsed.rows),
        "failed": sum(row.status == "failed" for row in parsed.rows),
        "copies": sum(row.fields["quantity"] for row in matched),
    }


def to_items(parsed: Parsed) -> list[dict]:
    """The matched rows, shaped for ``service.import_entries``."""
    return [{"card": row.card, **row.fields} for row in parsed.rows if row.status == "matched"]


def unresolved_csv(parsed: Parsed, describe) -> str:
    """The rows that were not imported, as they were, with what is wrong with each."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([*parsed.header, "Line", "Problem"])
    width = len(parsed.header)
    for row in parsed.rows:
        if row.status != "matched":
            cells = (row.raw + [""] * width)[: max(width, len(row.raw))]
            writer.writerow([*cells, row.line, describe(row)])
    return out.getvalue()
