"""Deck mode: which deck the search results' + and − buttons fill.

The choice is a cookie (SPEC §7, Deck mode). A remembered deck that no longer exists
falls back to the deck changed last.
"""

import sqlite3
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode

from fastapi import Request
from fastapi.responses import Response

from magicdb.decks import service

DECK_COOKIE = "magicdb_deckmode"
ONE_YEAR = 365 * 24 * 60 * 60


@dataclass(frozen=True)
class Choice:
    decks: list[sqlite3.Row]
    deck: sqlite3.Row | None  # None when there are no decks


def choice(request: Request, conn: sqlite3.Connection) -> Choice:
    decks = service.deck_choices(conn)
    wanted = (parse_qs(request.cookies.get(DECK_COOKIE, "")).get("deck") or [""])[0]
    deck = next((d for d in decks if str(d["id"]) == wanted), None)
    if deck is None and decks:
        deck = max(decks, key=lambda d: (d["updated_at"] or "", d["id"]))
    return Choice(decks=decks, deck=deck)


def remember(response: Response, deck_id: int | None) -> None:
    value = urlencode({"deck": "" if deck_id is None else str(deck_id)})
    response.set_cookie(DECK_COOKIE, value, max_age=ONE_YEAR, samesite="lax", path="/")
