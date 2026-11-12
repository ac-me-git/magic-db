"""Build the card catalog from Scryfall bulk data (SPEC §4)."""

import gzip
import json
import logging
import re
import sqlite3
import threading
import unicodedata
import uuid
from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import httpx
import ijson

from magicdb import __version__, db
from magicdb.catalog import publish
from magicdb.config import Settings

log = logging.getLogger(__name__)

API = "https://api.scryfall.com"
HEADERS = {"User-Agent": f"magic-db/{__version__}", "Accept": "application/json"}
BATCH = 5000
MIN_ROWS = 50_000

COLOR_BITS = {"W": 1, "U": 2, "B": 4, "R": 8, "G": 16}
RARITY_ORD = {"common": 0, "uncommon": 1, "rare": 2, "special": 3, "mythic": 4, "bonus": 5}
EXTRA_LAYOUTS = {
    "token",
    "double_faced_token",
    "emblem",
    "art_series",
    "planar",
    "scheme",
    "vanguard",
}
EXTRA_SET_TYPES = {"memorabilia", "token", "minigame"}
SUPERTYPES = {"Basic", "Legendary", "Snow", "World", "Ongoing", "Host"}
FANCY_FRAMES = {"showcase", "extendedart"}

COLUMNS = (
    "id",
    "oracle_id",
    "name",
    "name_norm",
    "layout",
    "mana_cost",
    "cmc",
    "type_line",
    "type_norm",
    "oracle_text",
    "oracle_norm",
    "flavor_text",
    "power",
    "toughness",
    "loyalty",
    "defense",
    "power_num",
    "toughness_num",
    "loyalty_num",
    "colors",
    "color_identity",
    "color_count",
    "keywords",
    "legal_in",
    "set_code",
    "set_name",
    "set_type",
    "collector_number",
    "collector_sort",
    "rarity",
    "rarity_ord",
    "released_at",
    "year",
    "artist",
    "finishes",
    "is_digital",
    "is_promo",
    "is_reprint",
    "is_extra",
    "price_eur",
    "price_eur_foil",
    "price_usd",
    "price_usd_foil",
    "price_usd_etched",
    "img_small",
    "img_normal",
    "img_back_small",
    "img_back_normal",
    "scryfall_uri",
    "faces_json",
    "pref",
)
_INSERT = (
    f"INSERT OR REPLACE INTO printings ({', '.join(COLUMNS)}) "
    f"VALUES ({', '.join(':' + c for c in COLUMNS)})"
)
_SQL_DIR = Path(__file__).parent
_LEADING_DIGITS = re.compile(r"^\d+")


@dataclass
class Progress:
    """Where a running update is, for the pages that show it. Read-only for them."""

    phase: str = "idle"  # idle | checking | downloading | building | indexing | publishing
    done: int = 0  # bytes downloaded, or printings read
    total: int = 0  # 0 when not known


progress = Progress()


def _step(phase: str, total: int = 0) -> None:
    progress.phase, progress.done, progress.total = phase, 0, total


@dataclass(frozen=True)
class UpdateResult:
    status: str  # ok | skipped | error
    message: str
    path: Path | None = None


# --- mapping one Scryfall card object to one row -------------------------------------


def normalize(text: str) -> str:
    """Lowercase and strip accents: 'Lim-Dûl' -> 'lim-dul'."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def color_mask(letters: Iterable[str]) -> int:
    mask = 0
    for letter in letters:
        mask |= COLOR_BITS.get(letter, 0)
    return mask


def _number(value: str | None) -> float | None:
    """'3' -> 3.0; '*', '1+*', None -> None."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _piped(values: Iterable[str]) -> str:
    """['Flying', 'First strike'] -> '|flying|first strike|'; nothing -> '|'."""
    return "|" + "".join(f"{v.lower()}|" for v in values)


def _joined(card: dict, faces: list[dict], key: str, separator: str) -> str:
    """Top-level field, or the faces' values joined when the top level has none."""
    if card.get(key) is not None:
        return card[key]
    return separator.join(face[key] for face in faces if face.get(key))


def _first(card: dict, faces: list[dict], key: str) -> str | None:
    """Top-level field, or the first face's."""
    if card.get(key) is not None:
        return card[key]
    return faces[0].get(key) if faces else None


def _price(prices: dict, key: str) -> float | None:
    return _number(prices.get(key))


def _pref(card: dict) -> int:
    """Higher = better printing to show for a card that has many (SPEC §4.3)."""
    plain_frame = (
        card.get("border_color") != "borderless"
        and not card.get("full_art")
        and not FANCY_FRAMES.intersection(card.get("frame_effects") or ())
    )
    score = (
        8 * (not card.get("digital"))
        + 4 * (not card.get("promo"))
        + 2 * plain_frame
        + 1 * (card.get("image_status") == "highres_scan")
    )
    return score * 100_000_000 + int(card["released_at"].replace("-", ""))


def is_extra(card: dict) -> bool:
    return card.get("layout") in EXTRA_LAYOUTS or card.get("set_type") in EXTRA_SET_TYPES


def map_card(card: dict) -> dict:
    """Turn a Scryfall card object into a ``printings`` row keyed by column name."""
    faces: list[dict] = card.get("card_faces") or []

    if card.get("colors") is not None:
        colors = color_mask(card["colors"])
    else:
        colors = color_mask(letter for face in faces for letter in face.get("colors") or ())

    front = card.get("image_uris")
    back: dict = {}
    if front is None:
        front = faces[0].get("image_uris") or {} if faces else {}
        back = faces[1].get("image_uris") or {} if len(faces) > 1 else {}

    power = _first(card, faces, "power")
    toughness = _first(card, faces, "toughness")
    loyalty = _first(card, faces, "loyalty")
    type_line = _joined(card, faces, "type_line", " // ")
    oracle_text = _joined(card, faces, "oracle_text", "\n//\n")
    legalities = card.get("legalities") or {}
    prices = card.get("prices") or {}
    number = card["collector_number"]
    leading = _LEADING_DIGITS.match(number)

    return {
        "id": card["id"],
        "oracle_id": card.get("oracle_id")
        or (faces[0].get("oracle_id") if faces else None)
        or card["id"],
        "name": card["name"],
        "name_norm": normalize(card["name"]),
        "layout": card.get("layout") or "normal",
        "mana_cost": _joined(card, faces, "mana_cost", " // ") or None,
        # reversible cards carry the mana value on their faces only
        "cmc": _first(card, faces, "cmc") or 0,
        "type_line": type_line,
        "type_norm": type_line.lower(),
        "oracle_text": oracle_text,
        "oracle_norm": oracle_text.lower(),
        "flavor_text": _first(card, faces, "flavor_text"),
        "power": power,
        "toughness": toughness,
        "loyalty": loyalty,
        "defense": _first(card, faces, "defense"),
        "power_num": _number(power),
        "toughness_num": _number(toughness),
        "loyalty_num": _number(loyalty),
        "colors": colors,
        "color_identity": color_mask(card.get("color_identity") or ()),
        "color_count": colors.bit_count(),
        "keywords": _piped(card.get("keywords") or ()),
        "legal_in": _piped(f for f, s in legalities.items() if s in ("legal", "restricted")),
        "set_code": card["set"],
        "set_name": card.get("set_name") or card["set"],
        "set_type": card.get("set_type") or "",
        "collector_number": number,
        "collector_sort": int(leading.group()) if leading else 0,
        "rarity": card.get("rarity") or "common",
        "rarity_ord": RARITY_ORD.get(card.get("rarity") or "", 0),
        "released_at": card["released_at"],
        "year": int(card["released_at"][:4]),
        "artist": card.get("artist"),
        "finishes": _piped(card.get("finishes") or ()),
        "is_digital": int(bool(card.get("digital"))),
        "is_promo": int(bool(card.get("promo"))),
        "is_reprint": int(bool(card.get("reprint"))),
        "is_extra": int(is_extra(card)),
        "price_eur": _price(prices, "eur"),
        "price_eur_foil": _price(prices, "eur_foil"),
        "price_usd": _price(prices, "usd"),
        "price_usd_foil": _price(prices, "usd_foil"),
        "price_usd_etched": _price(prices, "usd_etched"),
        "img_small": front.get("small"),
        "img_normal": front.get("normal"),
        "img_back_small": back.get("small"),
        "img_back_normal": back.get("normal"),
        "scryfall_uri": card.get("scryfall_uri"),
        "faces_json": json.dumps(faces, ensure_ascii=False) if faces else None,
        "pref": _pref(card),
    }


def type_tokens(type_line: str) -> set[tuple[str, str]]:
    """'Legendary Creature — Elder Dragon' -> supertype/type/subtype facet pairs."""
    tokens: set[tuple[str, str]] = set()
    for part in type_line.split(" // "):
        left, _, right = part.partition(" — ")
        for word in left.split():
            tokens.add(("supertype" if word in SUPERTYPES else "type", word))
        for word in right.split():
            tokens.add(("subtype", word))
    return tokens


# --- building the catalog file --------------------------------------------------------


def iter_cards(path: Path) -> Iterator[dict]:
    """Stream card objects out of a bulk file without loading it in memory.

    Scryfall has served bulk data both as one JSON array and as gzipped JSON Lines;
    the format is recognised from the content, not from the file name.
    """
    with path.open("rb") as probe:
        gzipped = probe.read(2) == b"\x1f\x8b"
    with gzip.open(path, "rb") if gzipped else path.open("rb") as handle:
        if handle.peek(64).lstrip()[:1] == b"[":
            yield from ijson.items(handle, "item", use_float=True)
            return
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _flush(conn: sqlite3.Connection, rows: list[dict], names: dict[str, str]) -> None:
    conn.executemany(_INSERT, rows)
    conn.executemany("INSERT OR IGNORE INTO names (name, name_norm) VALUES (?, ?)", names.items())
    rows.clear()
    names.clear()


def _insert_sets(conn: sqlite3.Connection, sets: Iterable[dict]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO sets "
        "(code, name, set_type, released_at, card_count, is_digital, parent_code, icon_svg_uri) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            (
                s["code"],
                s["name"],
                s.get("set_type") or "",
                s.get("released_at"),
                s.get("card_count") or 0,
                int(bool(s.get("digital"))),
                s.get("parent_set_code"),
                s.get("icon_svg_uri"),
            )
            for s in sets
        ),
    )
    # sets that have printings but were missing from the list
    conn.execute(
        "INSERT OR IGNORE INTO sets (code, name, set_type, released_at, is_digital) "
        "SELECT set_code, MIN(set_name), MIN(set_type), MIN(released_at), MIN(is_digital) "
        "FROM printings GROUP BY set_code"
    )
    conn.execute(
        "UPDATE sets SET catalog_count = (SELECT COUNT(DISTINCT collector_number) "
        "FROM printings WHERE printings.set_code = sets.code)"
    )


def build_catalog(
    cards: Iterable[dict], sets: Iterable[dict], dest: Path, bulk_updated_at: str
) -> int:
    """Write a complete catalog file at ``dest``; return the number of printings."""
    facets: Counter[tuple[str, str]] = Counter()
    seen_oracle: set[str] = set()
    rows: list[dict] = []
    names: dict[str, str] = {}

    with closing(sqlite3.connect(dest)) as conn:
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.executescript((_SQL_DIR / "schema.sql").read_text(encoding="utf-8"))
        conn.execute("BEGIN")
        for card in cards:
            if card.get("object", "card") != "card":
                continue
            row = map_card(card)
            rows.append(row)
            if not row["is_extra"]:
                names[row["name"]] = row["name_norm"]
                if row["oracle_id"] not in seen_oracle:
                    seen_oracle.add(row["oracle_id"])
                    facets.update(type_tokens(row["type_line"]))
                    facets.update(("keyword", k) for k in set(card.get("keywords") or ()))
                    facets.update(("format", f) for f in row["legal_in"].strip("|").split("|") if f)
            if len(rows) >= BATCH:
                progress.done += len(rows)
                _flush(conn, rows, names)
        _flush(conn, rows, names)

        _step("indexing")  # sets, facets, indexes: the slowest part, with nothing to count
        _insert_sets(conn, sets)
        conn.executemany(
            "INSERT INTO facets (kind, value, n) VALUES (?, ?, ?)",
            ((kind, value, n) for (kind, value), n in facets.items()),
        )
        count = conn.execute("SELECT COUNT(*) FROM printings").fetchone()[0]
        conn.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            [
                ("schema_version", "1"),
                ("bulk_updated_at", bulk_updated_at),
                ("built_at", db.utcnow()),
                ("row_count", str(count)),
            ],
        )
        conn.commit()
        conn.executescript((_SQL_DIR / "indexes.sql").read_text(encoding="utf-8"))
        conn.execute("ANALYZE")
        conn.commit()
    return count


# --- the update job -------------------------------------------------------------------


def _get_json(client: httpx.Client, url: str) -> dict:
    response = client.get(url)
    response.raise_for_status()
    return response.json()


def _download(client: httpx.Client, url: str, dest: Path) -> None:
    with client.stream("GET", url) as response:
        response.raise_for_status()
        _step("downloading", int(response.headers.get("content-length") or 0))
        with dest.open("wb") as handle:
            for chunk in response.iter_bytes(1 << 20):
                handle.write(chunk)
                progress.done += len(chunk)


def _update(
    settings: Settings,
    from_file: Path | None,
    sets_file: Path | None,
    client: httpx.Client | None,
    force: bool,
    min_rows: int,
) -> UpdateResult:
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    live = publish.live_catalog(settings)
    own_client: httpx.Client | None = None
    downloaded: Path | None = None
    build = settings.tmp_dir / f"build-{uuid.uuid4().hex}.db"
    try:
        if from_file is not None:
            source = from_file
            stamp = f"file:{from_file.name}"
            sets = json.loads(sets_file.read_text(encoding="utf-8")) if sets_file else []
        else:
            if client is None:
                client = own_client = httpx.Client(
                    headers=HEADERS,
                    timeout=httpx.Timeout(30, read=120),
                    follow_redirects=True,
                )
            _step("checking")
            bulk = _get_json(client, f"{API}/bulk-data")["data"]
            entry = next(item for item in bulk if item["type"] == "default_cards")
            stamp = entry["updated_at"]
            if (
                live is not None
                and not force
                and publish.read_meta(live).get("bulk_updated_at") == stamp
            ):
                return UpdateResult("skipped", "already up to date", live)
            sets = _get_json(client, f"{API}/sets")["data"]
            url = entry.get("jsonl_download_uri") or entry.get("download_uri")
            if not url:
                return UpdateResult("error", "Scryfall bulk-data entry has no download link", live)
            source = downloaded = settings.tmp_dir / "default_cards.download"
            _download(client, url, downloaded)

        _step("building", int(publish.read_meta(live).get("row_count", 0)) if live else 0)
        count = build_catalog(iter_cards(source), sets, build, stamp)
        _step("publishing")
        problem = publish.check(build, live, min_rows)
        if problem:
            return UpdateResult("error", f"new catalog rejected: {problem}", live)
        target = publish.publish(build, settings)
    finally:
        build.unlink(missing_ok=True)
        if downloaded is not None:
            downloaded.unlink(missing_ok=True)
        if own_client is not None:
            own_client.close()

    publish.cleanup_old(settings)
    return UpdateResult("ok", f"{count} printings", target)


_running = threading.Lock()


def is_running() -> bool:
    return _running.locked()


def run_update(
    settings: Settings,
    *,
    from_file: Path | None = None,
    sets_file: Path | None = None,
    client: httpx.Client | None = None,
    force: bool = False,
    min_rows: int = MIN_ROWS,
) -> UpdateResult:
    """Rebuild the catalog and record the run in ``job_runs``. Never raises.

    Whatever goes wrong, the previous catalog stays live.
    """
    if not _running.acquire(blocking=False):
        return UpdateResult("skipped", "an update is already running")
    try:
        db.migrate(settings)
        with closing(db.connect(settings)) as conn, conn:
            run_id = conn.execute(
                "INSERT INTO job_runs (job, started_at, status) VALUES ('catalog', ?, 'running')",
                (db.utcnow(),),
            ).lastrowid
        try:
            result = _update(settings, from_file, sets_file, client, force, min_rows)
        except Exception as exc:  # network, disk, malformed data: all end as a failed run
            log.exception("catalog update failed")
            result = UpdateResult("error", f"{type(exc).__name__}: {exc}")
        with closing(db.connect(settings)) as conn, conn:
            conn.execute(
                "UPDATE job_runs SET finished_at = ?, status = ?, message = ? WHERE id = ?",
                (db.utcnow(), result.status, result.message, run_id),
            )
        return result
    finally:
        _step("idle")
        _running.release()
