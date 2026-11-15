"""What may change: nothing, the collection or the decks (SPEC §7).

A three-way switch replaces the old safety lock. *Locked* keeps everything read-only;
*Collection input* lets the collection change (fast input in the search results, the
card page, import) and keeps the decks read-only; *Deck building* lets the decks change
(deck mode in the search results, the deck pages) and keeps the collection read-only.
The search results follow the switch: fast input or deck mode, or neither.

The state is one value for the whole server, kept in the ``settings`` table so that it
survives a restart, and mirrored in ``app.state.edit_mode`` so that reading it costs
nothing. Enforcement is in one place: a middleware in ``app.py`` refuses every request
that is not a plain read unless the switch allows what ``needs`` says the path needs.
A new page that changes the collection is therefore protected without anyone
remembering to do it.
"""

import sqlite3
from contextlib import closing
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from magicdb import db
from magicdb.config import Settings
from magicdb.templating import safe_next

router = APIRouter()
KEY = "edit_mode"
LOCKED, INPUT, DECK = "locked", "input", "deck"
STATES = (LOCKED, INPUT, DECK)


def load(settings: Settings) -> str:
    """The saved state. A database that never chose one starts locked."""
    with closing(db.connect(settings)) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (KEY,)).fetchone()
    return row["value"] if row is not None and row["value"] in STATES else LOCKED


def save(conn: sqlite3.Connection, state: str) -> None:
    with conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (KEY, state),
        )


def needs(path: str) -> str | None:
    """The state a change at ``path`` needs, or None when no state stops it.

    The switch itself, the choice of light or dark pages and the jobs of the
    administration page change nothing in the collection or the decks; every path
    under ``/deck`` changes a deck; anything else changes the collection.
    """
    if path in ("/mode", "/theme") or path.startswith("/admin/"):
        return None
    return DECK if path.startswith("/deck") else INPUT


@router.post("/mode")
def switch(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(db.get_conn)],
    state: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Move the switch and go back to the page the visitor was on."""
    if state in STATES:
        save(conn, state)
        request.app.state.edit_mode = state
    return RedirectResponse(safe_next(next, "/"), status_code=303)
