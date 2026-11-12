"""Turn a syntax tree into SQL (SPEC §6.2).

``FILTERS`` is the single table of what can be searched: it drives this compiler and the
``/syntax`` help page. Every user value becomes a bound parameter; the SQL text is built
only from constants in this module.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from magicdb.catalog.ingest import COLOR_BITS, RARITY_ORD, normalize
from magicdb.search.errors import QueryError
from magicdb.search.parser import And, Filter, Node, Not, Or, Word

Sql = tuple[str, list]

ALL_COLORS = 31
COMPARE = ("=", "!=", "<", "<=", ">", ">=")
NUMERIC_OPS = (":", *COMPARE)
LIKE = "LIKE ? ESCAPE '\\'"

ORDERS = ("name", "mv", "released", "rarity", "price", "set")
DESCENDING_BY_DEFAULT = ("released", "rarity", "price")
CONDITIONS = ("MT", "NM", "EX", "GD", "LP", "PL", "PO")
FINISHES = ("nonfoil", "foil", "etched")

_COLOR_NAMES = {"white": "w", "blue": "u", "black": "b", "red": "r", "green": "g"}
_RARITIES = {
    "c": "common",
    "u": "uncommon",
    "r": "rare",
    "s": "special",
    "m": "mythic",
    "b": "bonus",
}
_YES = ("yes", "y", "true", "1")
_NO = ("no", "n", "false", "0")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MANA_SHORTHAND = re.compile(r"\d+|[a-z]")


@dataclass
class Context:
    """What the query asked for besides the WHERE clause."""

    currency: str = "eur"
    unique: str = "cards"
    order: str = "name"
    direction: str | None = None
    show_extras: bool = False
    negated: bool = False  # true while compiling something under an odd number of "-"
    seen: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class FilterDef:
    name: str
    keys: tuple[str, ...]
    ops: tuple[str, ...]
    build: Callable[[Context, str, str], Sql | None]
    summary: str
    example: str
    directive: bool = False


# --- helpers --------------------------------------------------------------------------


def _contains(value: str) -> str:
    """A LIKE pattern matching ``value`` anywhere, with wildcards escaped."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _sql_op(op: str) -> str:
    return "=" if op == ":" else op


def _number(key: str, value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise QueryError(f"'{key}' needs a number, not '{value}'.") from None
    if number != number or number in (float("inf"), float("-inf")):
        raise QueryError(f"'{key}' needs a number, not '{value}'.")
    return number


def _choice(key: str, value: str, allowed: tuple[str, ...]) -> str:
    lowered = value.lower()
    if lowered not in allowed:
        raise QueryError(f"'{key}:' accepts {', '.join(allowed)}; got '{value}'.")
    return lowered


def _yes_no(key: str, value: str) -> bool:
    lowered = value.lower()
    if lowered in _YES:
        return True
    if lowered in _NO:
        return False
    raise QueryError(f"'{key}:' accepts yes or no; got '{value}'.")


# --- builders: each returns a function (ctx, op, value) -> (sql, params) ---------------


def text(column: str, norm: Callable[[str], str] = str.lower) -> Callable[..., Sql]:
    def build(ctx: Context, op: str, value: str) -> Sql:
        return f"IFNULL({column}, '') {LIKE}", [_contains(norm(value))]

    return build


def exact_name(value: str) -> Sql:
    """Full name, or the full name of one face of a two-faced card."""
    name = normalize(value)
    escaped = _contains(name)[1:-1]
    return (
        f"(p.name_norm = ? OR p.name_norm {LIKE} OR p.name_norm {LIKE})",
        [name, f"{escaped} // %", f"% // {escaped}"],
    )


def name(ctx: Context, op: str, value: str) -> Sql:
    if op == "=":
        return exact_name(value)
    return f"p.name_norm {LIKE}", [_contains(normalize(value))]


def numeric(column: str, label: str) -> Callable[..., Sql]:
    def build(ctx: Context, op: str, value: str) -> Sql:
        number = _number(label, value)
        # "+column" stops SQLite from using an index for a range: on the real catalog
        # "mv<=4" through ix_p_cmc is twice as slow as a plain scan
        target = column if op in (":", "=") else f"+{column}"
        return f"({column} IS NOT NULL AND {target} {_sql_op(op)} ?)", [number]

    return build


def piped(column: str) -> Callable[..., Sql]:
    """Membership in a '|a|b|' column."""

    def build(ctx: Context, op: str, value: str) -> Sql:
        return f"{column} {LIKE}", [_contains(f"|{value.lower()}|")]

    return build


def equals(column: str, *, shows_extras: bool = False) -> Callable[..., Sql]:
    def build(ctx: Context, op: str, value: str) -> Sql:
        if shows_extras and not ctx.negated and op != "!=":
            ctx.show_extras = True
        return f"{column} {'!=' if op == '!=' else '='} ?", [value.lower()]

    return build


def _color_value(key: str, value: str) -> int | str:
    """A bitmask, or 'colorless' / 'multicolor'."""
    lowered = value.lower()
    if lowered in ("c", "colorless", "colourless"):
        return "colorless"
    if lowered in ("m", "multi", "multicolor", "multicolour"):
        return "multicolor"
    letters = _COLOR_NAMES.get(lowered, lowered)
    mask = 0
    for letter in letters:
        bit = COLOR_BITS.get(letter.upper())
        if bit is None:
            raise QueryError(
                f"'{key}:' accepts the letters w, u, b, r, g, a colour name, "
                f"c (colourless) or m (multicolour); got '{value}'."
            )
        mask |= bit
    return mask


def colors(column: str, default_op: str, label: str) -> Callable[..., Sql]:
    several = f"({column} & ({column} - 1)) != 0"  # more than one bit set

    def build(ctx: Context, op: str, value: str) -> Sql:
        mask = _color_value(label, value)
        if op == ":":
            op = default_op
        if mask == "colorless":
            return (f"{column} != 0" if op == "!=" else f"{column} = 0"), []
        if mask == "multicolor":
            return (f"NOT ({several})" if op == "!=" else several), []
        outside = ALL_COLORS & ~mask
        if op in ("=", "!="):
            return f"{column} {op} ?", [mask]
        if op == ">=":
            return f"({column} & ?) = ?", [mask, mask]
        if op == "<=":
            return f"({column} & ?) = 0", [outside]
        if op == ">":
            return f"(({column} & ?) = ? AND {column} != ?)", [mask, mask, mask]
        return f"(({column} & ?) = 0 AND {column} != ?)", [outside, mask]

    return build


def mana(ctx: Context, op: str, value: str) -> Sql:
    if "{" in value:
        symbols = value.upper()
    else:
        parts = _MANA_SHORTHAND.findall(value.lower())
        if "".join(parts) != value.lower():
            raise QueryError(
                f"'m:' accepts symbols like {{2}}{{R}}{{R}} or the short form 2rr; got '{value}'."
            )
        symbols = "".join(f"{{{part.upper()}}}" for part in parts)
    return f"IFNULL(p.mana_cost, '') {LIKE}", [_contains(symbols)]


def rarity(ctx: Context, op: str, value: str) -> Sql:
    lowered = value.lower()
    full = _RARITIES.get(lowered, lowered)
    if full not in RARITY_ORD:
        raise QueryError(
            f"'r:' accepts common, uncommon, rare, mythic, special or bonus; got '{value}'."
        )
    return f"p.rarity_ord {_sql_op(op)} ?", [RARITY_ORD[full]]


def collector_number(ctx: Context, op: str, value: str) -> Sql:
    if not ctx.negated and op != "!=":
        ctx.show_extras = True
    if op in (":", "=", "!="):
        return f"LOWER(p.collector_number) {'!=' if op == '!=' else '='} ?", [value.lower()]
    return f"p.collector_sort {op} ?", [_number("cn", value)]


def date(ctx: Context, op: str, value: str) -> Sql:
    if not _DATE.match(value):
        raise QueryError(f"'date' needs a date like 2024-01-31, not '{value}'.")
    return f"p.released_at {_sql_op(op)} ?", [value]


_IS = {
    "foil": "p.finishes LIKE '%|foil|%'",
    "nonfoil": "p.finishes LIKE '%|nonfoil|%'",
    "etched": "p.finishes LIKE '%|etched|%'",
    "reprint": "p.is_reprint = 1",
    "promo": "p.is_promo = 1",
    "digital": "p.is_digital = 1",
    "extra": "p.is_extra = 1",
    "dfc": "p.img_back_normal IS NOT NULL",
}


def is_(ctx: Context, op: str, value: str) -> Sql:
    kind = _choice("is", value, tuple(_IS))
    if kind == "extra" and not ctx.negated:
        ctx.show_extras = True
    return _IS[kind], []


def have(ctx: Context, op: str, value: str) -> Sql:
    owned = "p.oracle_id IN (SELECT oracle_id FROM entries)"
    return (owned if _yes_no("have", value) else f"NOT ({owned})"), []


def have_print(ctx: Context, op: str, value: str) -> Sql:
    owned = "p.id IN (SELECT scryfall_id FROM entries)"
    return (owned if _yes_no("haveprint", value) else f"NOT ({owned})"), []


def quantity(ctx: Context, op: str, value: str) -> Sql:
    total = "(SELECT IFNULL(SUM(e.quantity), 0) FROM entries e WHERE e.scryfall_id = p.id)"
    return f"{total} {_sql_op(op)} ?", [_number("qty", value)]


def _owned(condition: str) -> str:
    return f"EXISTS (SELECT 1 FROM entries e WHERE e.scryfall_id = p.id AND {condition})"


def location(ctx: Context, op: str, value: str) -> Sql:
    sql = (
        "EXISTS (SELECT 1 FROM entries e JOIN locations l ON l.id = e.location_id "
        f"WHERE e.scryfall_id = p.id AND l.name {LIKE})"
    )
    return sql, [_contains(value)]


def condition(ctx: Context, op: str, value: str) -> Sql:
    if value.upper() not in CONDITIONS:
        raise QueryError(f"'cond:' accepts {', '.join(CONDITIONS)}; got '{value}'.")
    return _owned("e.condition = ?"), [value.upper()]


def finish(ctx: Context, op: str, value: str) -> Sql:
    return _owned("e.finish = ?"), [_choice("finish", value, FINISHES)]


def language(ctx: Context, op: str, value: str) -> Sql:
    return _owned("e.language = ?"), [value.lower()]


def deck(ctx: Context, op: str, value: str) -> Sql:
    sql = (
        "p.oracle_id IN (SELECT dc.oracle_id FROM deck_cards dc "
        f"JOIN decks d ON d.id = dc.deck_id WHERE d.name {LIKE})"
    )
    return sql, [_contains(value)]


def unique(ctx: Context, op: str, value: str) -> None:
    ctx.unique = _choice("unique", value, ("cards", "prints"))


def order(ctx: Context, op: str, value: str) -> None:
    lowered = {"cmc": "mv", "eur": "price", "usd": "price"}.get(value.lower(), value.lower())
    ctx.order = _choice("order", lowered, ORDERS)


def direction(ctx: Context, op: str, value: str) -> None:
    ctx.direction = _choice("dir", value, ("asc", "desc"))


def include(ctx: Context, op: str, value: str) -> None:
    _choice("include", value, ("extras",))
    ctx.show_extras = True


def _def(
    name_: str, keys: str, ops: tuple[str, ...], build: Callable, summary: str, example: str
) -> FilterDef:
    return FilterDef(name_, tuple(keys.split()), ops, build, summary, example)


def _directive(name_: str, build: Callable, summary: str, example: str) -> FilterDef:
    return FilterDef(name_, (name_,), (":",), build, summary, example, directive=True)


ONLY = (":",)
TEXT = (":", "=")
EQ = (":", "=", "!=")

FILTERS: tuple[FilterDef, ...] = (
    _def("name", "name", TEXT, name, "Card name contains (= for the exact name)", "name:bolt"),
    _def(
        "oracle", "o oracle", TEXT, text("p.oracle_norm"), "Rules text contains", 'o:"draw a card"'
    ),
    _def("type", "t type", TEXT, text("p.type_norm"), "Type line contains", "t:legendary t:dragon"),
    _def("keyword", "kw keyword", TEXT, piped("p.keywords"), "Has a keyword ability", "kw:flying"),
    _def(
        "color",
        "c color colour",
        NUMERIC_OPS,
        colors("p.colors", ">=", "c"),
        "Colours",
        "c:rg  c=w",
    ),
    _def(
        "identity",
        "id identity",
        NUMERIC_OPS,
        colors("p.color_identity", "<=", "id"),
        "Colour identity (fits in a deck of these colours)",
        "id:wub",
    ),
    _def("mv", "mv cmc", NUMERIC_OPS, numeric("p.cmc", "mv"), "Mana value", "mv<=3"),
    _def("mana", "m mana", TEXT, mana, "Mana cost contains", "m:2rr  m:{G}{G}"),
    _def("power", "pow power", NUMERIC_OPS, numeric("p.power_num", "pow"), "Power", "pow>=5"),
    _def(
        "toughness",
        "tou toughness",
        NUMERIC_OPS,
        numeric("p.toughness_num", "tou"),
        "Toughness",
        "tou<2",
    ),
    _def(
        "loyalty", "loy loyalty", NUMERIC_OPS, numeric("p.loyalty_num", "loy"), "Loyalty", "loy=3"
    ),
    _def("rarity", "r rarity", NUMERIC_OPS, rarity, "Rarity", "r:mythic  r>=rare"),
    _def(
        "set", "s e set edition", EQ, equals("p.set_code", shows_extras=True), "Set code", "set:mh3"
    ),
    _def("number", "cn number", NUMERIC_OPS, collector_number, "Collector number", "cn:161"),
    _def("settype", "st", EQ, equals("p.set_type"), "Set type", "st:expansion"),
    _def("format", "f format legal", TEXT, piped("p.legal_in"), "Legal in a format", "f:modern"),
    _def("artist", "a artist", TEXT, text("p.artist"), "Artist name contains", 'a:"rebecca guay"'),
    _def("flavor", "ft flavor", TEXT, text("p.flavor_text"), "Flavour text contains", "ft:urza"),
    _def(
        "year", "year", NUMERIC_OPS, numeric("p.year", "year"), "Year of the printing", "year>=2020"
    ),
    _def("date", "date", NUMERIC_OPS, date, "Release date of the printing", "date>=2024-01-01"),
    _def("eur", "eur", NUMERIC_OPS, numeric("p.price_eur", "eur"), "Price in euro", "eur<5"),
    _def(
        "usd", "usd", NUMERIC_OPS, numeric("p.price_usd", "usd"), "Price in US dollars", "usd>=20"
    ),
    _def("is", "is", ONLY, is_, "One of: " + ", ".join(_IS), "is:foil"),
    _def("have", "have", ONLY, have, "I own any printing of this card (yes / no)", "have:yes"),
    _def(
        "haveprint",
        "haveprint",
        ONLY,
        have_print,
        "I own this exact printing (yes / no)",
        "set:arn haveprint:no",
    ),
    _def("qty", "qty", NUMERIC_OPS, quantity, "Copies I own of this printing", "qty>=4"),
    _def(
        "loc",
        "loc location",
        TEXT,
        location,
        "I keep it in a location named like",
        'loc:"blue box"',
    ),
    _def("cond", "cond", TEXT, condition, "I own it in this condition", "cond:nm"),
    _def("finish", "finish", TEXT, finish, "I own it in this finish", "finish:foil"),
    _def("lang", "lang", TEXT, language, "I own it in this language", "lang:it"),
    _def("deck", "deck", TEXT, deck, "In a deck whose name contains", 'deck:"dragons"'),
    _directive("unique", unique, "Show cards (default) or every printing", "unique:prints"),
    _directive("order", order, "Sort by: " + ", ".join(ORDERS), "order:price"),
    _directive("dir", direction, "Sort direction: asc or desc", "dir:desc"),
    _directive("include", include, "Also show tokens and other extras", "include:extras"),
)

BY_KEY: dict[str, FilterDef] = {
    key: definition for definition in FILTERS for key in definition.keys
}


# --- walking the tree -----------------------------------------------------------------


def _filter(node: Filter, ctx: Context) -> Sql | None:
    definition = BY_KEY.get(node.key)
    if definition is None:
        raise QueryError(f"'{node.key}' is not something you can search by.")
    if node.op not in definition.ops:
        raise QueryError(f"'{node.key}' cannot be used with '{node.op}'.")
    if definition.directive:
        if ctx.negated:
            raise QueryError(f"'{node.key}:' cannot be used after '-'.")
        if definition.name in ctx.seen:
            raise QueryError(f"'{node.key}:' is given more than once.")
        ctx.seen.add(definition.name)
    return definition.build(ctx, node.op, node.value)


def _walk(node: Node, ctx: Context) -> Sql | None:
    if isinstance(node, Word):
        if node.exact:
            return exact_name(node.text)
        return f"p.name_norm {LIKE}", [_contains(normalize(node.text))]
    if isinstance(node, Filter):
        return _filter(node, ctx)
    if isinstance(node, Not):
        ctx.negated = not ctx.negated
        try:
            sql, params = _walk(node.node, ctx)
        finally:
            ctx.negated = not ctx.negated
        return f"NOT ({sql})", params
    parts = [_walk(child, ctx) for child in node.nodes]
    parts = [part for part in parts if part is not None]
    if not parts:
        return None
    joiner = " OR " if isinstance(node, Or) else " AND "
    sql = joiner.join(part[0] for part in parts)
    return f"({sql})", [param for part in parts for param in part[1]]


def is_only_words(node: Node) -> bool:
    """True for a search made only of plain name words (eligible for 'did you mean')."""
    if isinstance(node, Word):
        return not node.exact
    return isinstance(node, And) and bool(node.nodes) and all(is_only_words(n) for n in node.nodes)


def compile_query(node: Node, currency: str = "eur") -> tuple[str, list, Context]:
    """Return ``(where_sql, params, context)`` for a parsed search.

    The WHERE clause refers to the catalog row as ``p`` and to the collection tables
    ``entries`` and ``locations`` by their plain names.
    """
    ctx = Context(currency=currency)
    compiled = _walk(node, ctx)
    clauses, params = ([compiled[0]], compiled[1]) if compiled else ([], [])
    if not ctx.show_extras:
        clauses.append("p.is_extra = 0")
    return " AND ".join(clauses) or "1", params, ctx
