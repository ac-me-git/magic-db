"""The safety lock: a switch that makes the collection read-only (SPEC §7).

While it is on, cards and the collection can be searched, browsed, exported and backed
up, but nothing can change the collection. The state is one value for the whole server,
kept in the ``settings`` table so that it survives a restart, and mirrored in
``app.state.locked`` so that reading it costs nothing.

Enforcement is in one place: a middleware in ``app.py`` refuses every request that is
not a plain read, except the ones listed in ``allowed_while_locked``. A new page that
changes the collection is therefore locked without anyone remembering to do it.
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
KEY = "locked"


def load(settings: Settings) -> bool:
    """The saved state. A collection that was never locked is unlocked."""
    with closing(db.connect(settings)) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (KEY,)).fetchone()
    return row is not None and row["value"] == "1"


def save(conn: sqlite3.Connection, locked: bool) -> None:
    with conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (KEY, "1" if locked else "0"),
        )


def allowed_while_locked(path: str) -> bool:
    """Requests that change something, but not the collection.

    The switch itself, and the jobs of the administration page (updating the card
    catalog, making a backup).
    """
    return path == "/lock" or path.startswith("/admin/")


@router.post("/lock")
def switch(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(db.get_conn)],
    state: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Turn the lock on or off and go back to the page the visitor was on."""
    if state in ("on", "off"):
        save(conn, state == "on")
        request.app.state.locked = state == "on"
    return RedirectResponse(safe_next(next, "/collection"), status_code=303)
