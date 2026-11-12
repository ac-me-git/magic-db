"""What the collection forms share: choices, and the values that stick between adds."""

import sqlite3
from urllib.parse import parse_qs, urlencode

from fastapi import Request
from fastapi.responses import Response

from magicdb.collection import service
from magicdb.collection.service import CollectionError

STICKY_COOKIE = "magicdb_add"
ONE_YEAR = 365 * 24 * 60 * 60
DEFAULTS = {"finish": "nonfoil", "condition": "NM", "language": "en", "location": "", "set": ""}
_ALLOWED = {
    "finish": service.FINISHES,
    "condition": service.CONDITIONS,
    "language": service.LANGUAGES,
}


def read_sticky(request: Request) -> dict[str, str]:
    """Finish, condition, language, location and set of the last card added here."""
    sticky = dict(DEFAULTS)
    stored = parse_qs(request.cookies.get(STICKY_COOKIE, ""))
    for key in DEFAULTS:
        value = (stored.get(key) or [""])[0]
        if key in _ALLOWED:
            if value in _ALLOWED[key]:
                sticky[key] = value
        elif value.isascii() and value.isalnum() and len(value) <= 10:
            sticky[key] = value  # a location id or a set code
    return sticky


def write_sticky(response: Response, **values: object) -> None:
    kept = {key: str(values.get(key) or "") for key in DEFAULTS}
    response.set_cookie(STICKY_COOKIE, urlencode(kept), max_age=ONE_YEAR, samesite="lax", path="/")


def location_id(value: str) -> int | None:
    """A location field as submitted: '' or 'none' is 'unassigned', else an id."""
    if value in ("", service.UNASSIGNED):
        return None
    if value.isascii() and value.isdigit() and len(value) < 10:
        return int(value)
    raise CollectionError("unknown_location")


def options(conn: sqlite3.Connection) -> dict[str, object]:
    return {
        "finishes": service.FINISHES,
        "conditions": service.CONDITIONS,
        "languages": service.LANGUAGES,
        "kinds": service.KINDS,
        "sorts": service.SORTS,
        "locations": conn.execute(
            "SELECT id, name FROM locations ORDER BY sort_order, id"
        ).fetchall(),
    }
