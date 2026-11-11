"""Card search: one query language for the search box and the filter panel (SPEC §6)."""

import sqlite3
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from magicdb import db
from magicdb.catalog.ingest import normalize
from magicdb.search.compiler import (
    DESCENDING_BY_DEFAULT,
    FILTERS,
    Context,
    compile_query,
    is_only_words,
)
from magicdb.search.errors import CatalogUnavailable, QueryError
from magicdb.search.parser import parse

__all__ = ["FILTERS", "CatalogUnavailable", "QueryError", "Result", "run", "suggest"]

PER_PAGE = 60
SUGGESTIONS = 5
SUGGESTION_CUTOFF = 75

# (sql expression, follows the chosen direction)
_ORDER_KEYS: dict[str, list[tuple[str, bool]]] = {
    "name": [("p.name_norm", True)],
    "mv": [("p.cmc", True)],
    "released": [("p.released_at", True)],
    "rarity": [("p.rarity_ord", True)],
    "set": [("p.set_code", True), ("p.collector_sort", True), ("p.collector_number", True)],
}
_TIE_BREAK = [("p.name_norm", False), ("p.released_at", False), ("p.id", False)]


@dataclass
class Result:
    rows: list[sqlite3.Row]
    total: int
    page: int
    pages: int
    unique: str  # "cards" | "prints"
    order: str
    direction: str  # "asc" | "desc"
    suggestions: list[str] = field(default_factory=list)


def _order_keys(ctx: Context) -> list[tuple[str, str]]:
    """``[(expression, 'ASC' | 'DESC')]`` for the chosen order, plus tie-breakers."""
    direction = ctx.direction or ("desc" if ctx.order in DESCENDING_BY_DEFAULT else "asc")
    if ctx.order == "price":
        column = "p.price_usd" if ctx.currency == "usd" else "p.price_eur"
        keys = [(f"{column} IS NULL", False), (column, True)]  # unpriced cards last
    else:
        keys = _ORDER_KEYS[ctx.order]
    chosen = [(expr, direction.upper() if follows else "ASC") for expr, follows in keys]
    return chosen + [(expr, "ASC") for expr, _ in _TIE_BREAK]


def suggest(conn: sqlite3.Connection, text: str) -> list[str]:
    """Card names that look like ``text``, best first ("did you mean…")."""
    names = dict(conn.execute("SELECT name, name_norm FROM cat.names"))
    hits = process.extract(
        normalize(text),
        names,
        scorer=fuzz.WRatio,
        limit=SUGGESTIONS,
        score_cutoff=SUGGESTION_CUTOFF,
    )
    return [name for _, _, name in hits]


def run(
    conn: sqlite3.Connection,
    query: str,
    page: int = 1,
    *,
    currency: str = "eur",
    per_page: int = PER_PAGE,
) -> Result:
    """Run a search and return one page of ``cat.printings`` rows.

    Raises ``QueryError`` (message fit for the user) when the text cannot be understood.
    """
    if not db.has_catalog(conn):
        raise CatalogUnavailable("the card catalog has not been downloaded yet")
    node = parse(query)
    where, params, ctx = compile_query(node, currency)
    page = max(1, page)

    keys = _order_keys(ctx)
    key_columns = ", ".join(f"{expr} AS k{i}" for i, (expr, _) in enumerate(keys))
    order_by = ", ".join(f"k{i} {way}" for i, (_, way) in enumerate(keys))
    if ctx.unique == "cards":
        # One row per card: the bare columns come from the row with the highest pref.
        # "+p.oracle_id" keeps SQLite from walking the oracle index, which is 3-5x slower
        # than a plain scan on the real catalog.
        hits = (
            f"SELECT p.id AS id, {key_columns}, MAX(p.pref) AS best "
            f"FROM cat.printings p WHERE {where} GROUP BY +p.oracle_id"
        )
    else:
        hits = f"SELECT p.id AS id, {key_columns} FROM cat.printings p WHERE {where}"

    found = conn.execute(
        f"SELECT id, COUNT(*) OVER () FROM ({hits}) ORDER BY {order_by} LIMIT ? OFFSET ?",
        [*params, per_page, (page - 1) * per_page],
    ).fetchall()
    if found:
        total = found[0][1]
    elif page > 1:  # past the last page: the window count is not available
        total = conn.execute(f"SELECT COUNT(*) FROM ({hits})", params).fetchone()[0]
    else:
        total = 0

    ids = [row[0] for row in found]
    rows: list[sqlite3.Row] = []
    if ids:
        marks = ", ".join("?" * len(ids))
        by_id = {
            row["id"]: row
            for row in conn.execute(f"SELECT * FROM cat.printings WHERE id IN ({marks})", ids)
        }
        rows = [by_id[card_id] for card_id in ids]

    suggestions: list[str] = []
    if total == 0 and is_only_words(node):
        suggestions = suggest(conn, query)

    return Result(
        rows=rows,
        total=total,
        page=page,
        pages=max(1, -(-total // per_page)),
        unique=ctx.unique,
        order=ctx.order,
        direction=keys[0][1].lower() if ctx.order != "price" else keys[1][1].lower(),
        suggestions=suggestions,
    )
