"""Decks: reads and writes, and how each deck compares with the collection (SPEC §14).

A deck lists cards (oracle ids) with a quantity, in a main board and a sideboard. It
never touches the collection. What is owned of a card is every copy in the collection,
whatever the printing, finish or location.

Words used here and on the pages:

- *missing*: the collection has fewer copies than this deck asks for;
- *contested*: enough for this deck alone, but not for all the decks that want the
  card put together.
"""

import sqlite3
from dataclasses import dataclass, field

from magicdb import db
from magicdb.collection import service as collection
from magicdb.collection.service import CollectionError
from magicdb.decks import text
from magicdb.search import suggest
from magicdb.search.compiler import exact_name
from magicdb.stats import TYPE_ORDER, types_of

BOARDS = ("main", "side")
MAX_NAME = 80
MAX_QUANTITY = 999
CHUNK = 400  # ids per IN (...) list


@dataclass(frozen=True)
class Unknown:
    """A line of an imported list that is not a card."""

    number: int
    text: str
    suggestions: list[str]


@dataclass(frozen=True)
class ImportReport:
    cards: int  # lines added
    copies: int
    unknown: list[Unknown]


@dataclass
class Row:
    """A card of a deck, compared with the collection and with the other decks."""

    id: int
    oracle_id: str
    name: str
    quantity: int
    board: str
    owned: int = 0  # copies in the collection
    have: int = 0  # of those, how many this row can count on
    wanted: int = 0  # copies all decks together ask for
    others: list[tuple[int, str, int]] = field(default_factory=list)  # (deck id, name, copies)
    printing: str | None = None  # a printing to link to and show
    type_line: str = ""
    mana_cost: str | None = None
    cmc: float = 0
    price: float | None = None  # cheapest paper printing

    @property
    def missing(self) -> int:
        return self.quantity - self.have

    @property
    def contested(self) -> bool:
        return bool(self.others) and self.owned < self.wanted and not self.missing

    @property
    def group(self) -> str:
        """The card type the row is listed under."""
        kinds = types_of({"type_line": self.type_line})
        return next((kind for kind in TYPE_ORDER if kind in kinds), "Other")


@dataclass
class Summary:
    id: int
    name: str
    format: str | None
    notes: str | None
    copies: int = 0
    cards: int = 0  # distinct cards
    have: int = 0
    contested: int = 0  # cards short only because other decks want them too

    @property
    def missing(self) -> int:
        return self.copies - self.have

    @property
    def percent(self) -> int:
        """Rounded down, so 100 means every card is there."""
        return 100 * self.have // self.copies if self.copies else 0

    @property
    def complete(self) -> bool:
        return self.copies > 0 and self.have == self.copies


@dataclass
class Shortage:
    """A card that several decks want and the collection cannot give to all of them."""

    oracle_id: str
    name: str
    owned: int
    wanted: int
    decks: list[tuple[int, str, int]]  # (deck id, name, copies)


# --- validation -----------------------------------------------------------------------


def _deck_fields(name: object, fmt: object, notes: object) -> tuple[str, str | None, str | None]:
    # no double quote, so that deck:"name" can always be written in a search
    name = " ".join(str(name or "").replace('"', "'").split())
    if not name or len(name) > MAX_NAME:
        raise CollectionError("bad_deck_name")
    return name, (str(fmt or "").strip())[:40] or None, (str(notes or "").strip())[:500] or None


def _quantity(value: object) -> int:
    try:
        quantity = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        raise CollectionError("bad_deck_quantity") from None
    if not 1 <= quantity <= MAX_QUANTITY:
        raise CollectionError("bad_deck_quantity")
    return quantity


def _board(value: object) -> str:
    if value not in BOARDS:
        raise CollectionError("bad_board")
    return str(value)


def get_deck(conn: sqlite3.Connection, deck_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM decks WHERE id = ?", (deck_id,)).fetchone()
    if row is None:
        raise CollectionError("unknown_deck")
    return row


def _touch(conn: sqlite3.Connection, deck_id: int) -> None:
    conn.execute("UPDATE decks SET updated_at = ? WHERE id = ?", (db.utcnow(), deck_id))


# --- finding cards in the catalog -----------------------------------------------------


def find_card(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    """The card with exactly this name (or with a face of this name): oracle id and
    full name. Tokens and other extras are not cards one puts in a deck."""
    if not name.strip() or not db.has_catalog(conn):
        return None
    where, params = exact_name(name.strip())
    return conn.execute(
        f"SELECT p.oracle_id, p.name, MAX(p.pref) FROM cat.printings p "
        f"WHERE {where} AND p.is_extra = 0 GROUP BY p.oracle_id ORDER BY MAX(p.pref) DESC LIMIT 1",
        params,
    ).fetchone()


def card_of_printing(conn: sqlite3.Connection, scryfall_id: str) -> sqlite3.Row | None:
    if not db.has_catalog(conn):
        return None
    return conn.execute(
        "SELECT oracle_id, name FROM cat.printings WHERE id = ?", (scryfall_id,)
    ).fetchone()


def _chunks(ids: list[str]) -> list[list[str]]:
    return [ids[start : start + CHUNK] for start in range(0, len(ids), CHUNK)]


def _catalog_facts(conn: sqlite3.Connection, ids: list[str], currency: str) -> dict[str, dict]:
    """For each card: its preferred printing's facts and its cheapest paper price."""
    facts: dict[str, dict] = {}
    if not ids or not db.has_catalog(conn):
        return facts
    column = "price_usd" if currency == "usd" else "price_eur"  # fixed names
    for chunk in _chunks(ids):
        marks = ", ".join("?" * len(chunk))
        for row in conn.execute(
            f"SELECT p.oracle_id, p.id, p.type_line, p.mana_cost, p.cmc, MAX(p.pref) "
            f"FROM cat.printings p WHERE p.oracle_id IN ({marks}) GROUP BY p.oracle_id",
            chunk,
        ):
            facts[row["oracle_id"]] = {
                "printing": row["id"],
                "type_line": row["type_line"],
                "mana_cost": row["mana_cost"],
                "cmc": row["cmc"],
                "price": None,
            }
        for row in conn.execute(
            f"SELECT p.oracle_id, MIN(p.{column}) AS price FROM cat.printings p "
            f"WHERE p.oracle_id IN ({marks}) AND p.is_digital = 0 GROUP BY p.oracle_id",
            chunk,
        ):
            if row["oracle_id"] in facts:
                facts[row["oracle_id"]]["price"] = row["price"]
    return facts


# --- writes ---------------------------------------------------------------------------


def create_deck(
    conn: sqlite3.Connection, name: object, fmt: object = None, notes: object = None
) -> int:
    name, fmt, notes = _deck_fields(name, fmt, notes)
    now = db.utcnow()
    with conn:
        try:
            cursor = conn.execute(
                "INSERT INTO decks (name, format, notes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, fmt, notes, now, now),
            )
        except sqlite3.IntegrityError:
            raise CollectionError("deck_exists", name=name) from None
    return cursor.lastrowid


def update_deck(
    conn: sqlite3.Connection, deck_id: int, name: object, fmt: object = None, notes: object = None
) -> None:
    name, fmt, notes = _deck_fields(name, fmt, notes)
    with conn:
        get_deck(conn, deck_id)
        try:
            conn.execute(
                "UPDATE decks SET name = ?, format = ?, notes = ?, updated_at = ? WHERE id = ?",
                (name, fmt, notes, db.utcnow(), deck_id),
            )
        except sqlite3.IntegrityError:
            raise CollectionError("deck_exists", name=name) from None


def delete_deck(conn: sqlite3.Connection, deck_id: int) -> None:
    """Delete a deck and its list. The collection is not touched."""
    with conn:
        get_deck(conn, deck_id)
        conn.execute("DELETE FROM deck_cards WHERE deck_id = ?", (deck_id,))
        conn.execute("DELETE FROM decks WHERE id = ?", (deck_id,))


def _put(
    conn: sqlite3.Connection, deck_id: int, oracle_id: str, name: str, quantity: int, board: str
) -> None:
    """Add copies of a card to a board; a card already there gets more copies."""
    conn.execute(
        "INSERT INTO deck_cards (deck_id, oracle_id, name, quantity, board) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (deck_id, oracle_id, board) DO UPDATE SET "
        "quantity = MIN(quantity + excluded.quantity, ?)",
        (deck_id, oracle_id, name, quantity, board, MAX_QUANTITY),
    )


def add_card(
    conn: sqlite3.Connection,
    deck_id: int,
    *,
    name: str = "",
    printing: str = "",
    quantity: object = 1,
    board: object = "main",
) -> str:
    """Add a card, given by exact name or by one of its printings. Returns its name."""
    quantity, board = _quantity(quantity), _board(board)
    with conn:
        get_deck(conn, deck_id)
        card = card_of_printing(conn, printing) if printing else find_card(conn, name)
        if card is None:
            raise CollectionError("unknown_deck_card", name=name.strip())
        _put(conn, deck_id, card["oracle_id"], card["name"], quantity, board)
        _touch(conn, deck_id)
    return card["name"]


def _deck_card(conn: sqlite3.Connection, deck_id: int, card_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM deck_cards WHERE id = ? AND deck_id = ?", (card_id, deck_id)
    ).fetchone()
    if row is None:
        raise CollectionError("unknown_deck_row")
    return row


def update_card(
    conn: sqlite3.Connection, deck_id: int, card_id: int, quantity: object, board: object
) -> None:
    """Set the copies of a row and the board it is in. Moving it onto a board where
    the card already is adds the two together."""
    quantity, board = _quantity(quantity), _board(board)
    with conn:
        row = _deck_card(conn, deck_id, card_id)
        already_there = conn.execute(
            "SELECT id FROM deck_cards "
            "WHERE deck_id = ? AND oracle_id = ? AND board = ? AND id != ?",
            (deck_id, row["oracle_id"], board, card_id),
        ).fetchone()
        if already_there is None:  # the row keeps its id, whatever changes
            conn.execute(
                "UPDATE deck_cards SET quantity = ?, board = ? WHERE id = ?",
                (quantity, board, card_id),
            )
        else:
            conn.execute("DELETE FROM deck_cards WHERE id = ?", (card_id,))
            _put(conn, deck_id, row["oracle_id"], row["name"], quantity, board)
        _touch(conn, deck_id)


def remove_card(conn: sqlite3.Connection, deck_id: int, card_id: int) -> None:
    with conn:
        _deck_card(conn, deck_id, card_id)
        conn.execute("DELETE FROM deck_cards WHERE id = ?", (card_id,))
        _touch(conn, deck_id)


def import_text(conn: sqlite3.Connection, deck_id: int, listing: str) -> ImportReport:
    """Add the cards of a pasted deck list. Lines that are not cards are reported, not
    guessed; everything else is added."""
    if not db.has_catalog(conn):
        raise CollectionError("no_catalog")
    found: dict[str, sqlite3.Row | None] = {}
    unknown: list[Unknown] = []
    cards = copies = 0
    with conn:
        get_deck(conn, deck_id)
        for line in text.parse(listing):
            key = line.name.lower()
            if key not in found:
                found[key] = find_card(conn, line.name)
            card = found[key]
            if card is None:
                unknown.append(Unknown(line.number, line.text, suggest(conn, line.name)[:3]))
                continue
            quantity = min(line.quantity, MAX_QUANTITY)
            if quantity < 1:
                continue
            _put(conn, deck_id, card["oracle_id"], card["name"], quantity, line.board)
            cards += 1
            copies += quantity
        if cards:
            _touch(conn, deck_id)
    return ImportReport(cards, copies, unknown)


def restore(conn: sqlite3.Connection, dump: list[dict]) -> list[str]:
    """Recreate the decks of a JSON export. A deck whose name already exists is left
    as it is. Returns the names of the decks created."""
    created = []
    with conn:
        existing = {row[0].lower() for row in conn.execute("SELECT name FROM decks")}
        for item in dump:
            try:
                name, fmt, notes = _deck_fields(
                    item.get("name"), item.get("format"), item.get("notes")
                )
            except CollectionError:
                continue
            if name.lower() in existing:
                continue
            now = db.utcnow()
            deck_id = conn.execute(
                "INSERT INTO decks (name, format, notes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, fmt, notes, now, now),
            ).lastrowid
            for card in item.get("cards") or []:
                board = card.get("board") if card.get("board") in BOARDS else "main"
                quantity = card.get("quantity")
                if not isinstance(quantity, int) or not 1 <= quantity <= MAX_QUANTITY:
                    continue
                if card.get("oracle_id") and card.get("name"):
                    _put(conn, deck_id, str(card["oracle_id"]), str(card["name"]), quantity, board)
            existing.add(name.lower())
            created.append(name)
    return created


# --- reads ----------------------------------------------------------------------------


def _demand(conn: sqlite3.Connection) -> dict[str, dict[int, int]]:
    """For every card in some deck: how many copies each deck wants (both boards)."""
    demand: dict[str, dict[int, int]] = {}
    for row in conn.execute(
        "SELECT oracle_id, deck_id, SUM(quantity) AS copies FROM deck_cards "
        "GROUP BY oracle_id, deck_id"
    ):
        demand.setdefault(row["oracle_id"], {})[row["deck_id"]] = row["copies"]
    return demand


def _owned(conn: sqlite3.Connection, ids: list[str]) -> dict[str, int]:
    owned: dict[str, int] = {}
    for chunk in _chunks(ids):
        owned.update(collection.owned_counts(conn, chunk, "cards"))
    return owned


def _deck_names(conn: sqlite3.Connection) -> dict[int, str]:
    return dict(conn.execute("SELECT id, name FROM decks"))


def list_decks(conn: sqlite3.Connection) -> list[Summary]:
    """Every deck with its completion, by name."""
    demand = _demand(conn)
    owned = _owned(conn, list(demand))
    decks = {
        row["id"]: Summary(row["id"], row["name"], row["format"], row["notes"])
        for row in conn.execute("SELECT * FROM decks ORDER BY name COLLATE NOCASE")
    }
    for oracle_id, wanted_by in demand.items():
        have, wanted = owned.get(oracle_id, 0), sum(wanted_by.values())
        for deck_id, copies in wanted_by.items():
            deck = decks[deck_id]
            deck.copies += copies
            deck.cards += 1
            deck.have += min(have, copies)
            if len(wanted_by) > 1 and have < wanted and have >= copies:
                deck.contested += 1
    return list(decks.values())


def summary(conn: sqlite3.Connection, deck_id: int) -> Summary:
    get_deck(conn, deck_id)
    return next(deck for deck in list_decks(conn) if deck.id == deck_id)


def deck_rows(conn: sqlite3.Connection, deck_id: int, currency: str = "eur") -> list[Row]:
    """The cards of a deck, each compared with the collection and the other decks.

    Sorted by board, then card type, mana value and name. A card in both boards counts
    its copies in the main board first.
    """
    get_deck(conn, deck_id)
    rows = [
        Row(r["id"], r["oracle_id"], r["name"], r["quantity"], r["board"])
        for r in conn.execute(
            "SELECT * FROM deck_cards WHERE deck_id = ? ORDER BY board, name, id", (deck_id,)
        )
    ]
    ids = sorted({row.oracle_id for row in rows})
    demand, names = _demand(conn), _deck_names(conn)
    owned, facts = _owned(conn, ids), _catalog_facts(conn, ids, currency)
    left = dict(owned)  # copies not yet counted on by a row of this deck
    for row in rows:
        wanted_by = demand.get(row.oracle_id, {})
        row.owned = owned.get(row.oracle_id, 0)
        row.wanted = sum(wanted_by.values())
        row.have = min(left.get(row.oracle_id, 0), row.quantity)
        left[row.oracle_id] = left.get(row.oracle_id, 0) - row.have
        row.others = sorted(
            (other, names[other], copies) for other, copies in wanted_by.items() if other != deck_id
        )
        for key, value in facts.get(row.oracle_id, {}).items():
            setattr(row, key, value)
    order = {kind: index for index, kind in enumerate((*TYPE_ORDER, "Other"))}
    rows.sort(key=lambda r: (r.board, order[r.group], r.cmc, r.name.lower(), r.id))
    return rows


def shortages(conn: sqlite3.Connection) -> list[Shortage]:
    """Cards wanted by two or more decks that the collection cannot give to all."""
    demand = {oracle: decks for oracle, decks in _demand(conn).items() if len(decks) > 1}
    owned, names = _owned(conn, list(demand)), _deck_names(conn)
    card_names = dict(
        conn.execute("SELECT oracle_id, MIN(name) FROM deck_cards GROUP BY oracle_id")
    )
    short = [
        Shortage(
            oracle_id,
            card_names[oracle_id],
            owned.get(oracle_id, 0),
            sum(wanted_by.values()),
            sorted((deck, names[deck], copies) for deck, copies in wanted_by.items()),
        )
        for oracle_id, wanted_by in demand.items()
        if owned.get(oracle_id, 0) < sum(wanted_by.values())
    ]
    return sorted(short, key=lambda s: (s.owned - s.wanted, s.name.lower()))


def decks_with_card(conn: sqlite3.Connection, oracle_id: str) -> list[sqlite3.Row]:
    """The decks a card is in, with how many copies each wants."""
    return conn.execute(
        "SELECT d.id, d.name, SUM(c.quantity) AS copies FROM deck_cards c "
        "JOIN decks d ON d.id = c.deck_id WHERE c.oracle_id = ? "
        "GROUP BY d.id ORDER BY d.name COLLATE NOCASE",
        (oracle_id,),
    ).fetchall()


def export_text(conn: sqlite3.Connection, deck_id: int) -> str:
    """The deck as a plain list that this app, Arena and most sites can read back."""
    get_deck(conn, deck_id)
    lines: list[str] = []
    for board, title in (("main", "Deck"), ("side", "Sideboard")):
        cards = conn.execute(
            "SELECT quantity, name FROM deck_cards WHERE deck_id = ? AND board = ? "
            "ORDER BY name COLLATE NOCASE",
            (deck_id, board),
        ).fetchall()
        if cards:
            lines += ([""] if lines else []) + [title]
            lines += [f"{card['quantity']} {card['name']}" for card in cards]
    return "\n".join(lines) + "\n"


def dump(conn: sqlite3.Connection) -> list[dict]:
    """Every deck, for the JSON export."""
    decks = []
    for deck in conn.execute("SELECT * FROM decks ORDER BY name COLLATE NOCASE").fetchall():
        cards = conn.execute(
            "SELECT name, oracle_id, quantity, board FROM deck_cards WHERE deck_id = ? "
            "ORDER BY board, name COLLATE NOCASE",
            (deck["id"],),
        )
        decks.append(
            {
                "name": deck["name"],
                "format": deck["format"],
                "notes": deck["notes"],
                "cards": [dict(card) for card in cards],
            }
        )
    return decks
