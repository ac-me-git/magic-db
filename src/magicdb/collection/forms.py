"""What the collection forms share: choices, and the finish that sticks between adds."""

from urllib.parse import parse_qs, urlencode

from fastapi import Request
from fastapi.responses import Response

from magicdb.collection import service

STICKY_COOKIE = "magicdb_add"
ONE_YEAR = 365 * 24 * 60 * 60
DEFAULTS = {"finish": "nonfoil"}


def read_sticky(request: Request) -> dict[str, str]:
    """The finish of the last copies added from a card's page."""
    stored = parse_qs(request.cookies.get(STICKY_COOKIE, ""))
    finish = (stored.get("finish") or [""])[0]
    return {"finish": finish if finish in service.FINISHES else DEFAULTS["finish"]}


def write_sticky(response: Response, finish: object) -> None:
    value = urlencode({"finish": str(finish or "")})
    response.set_cookie(STICKY_COOKIE, value, max_age=ONE_YEAR, samesite="lax", path="/")


def options() -> dict[str, object]:
    return {"finishes": service.FINISHES}
