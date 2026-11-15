"""What the collection forms share: choices, and the finish that sticks between adds."""

from urllib.parse import parse_qs, urlencode

from fastapi import Request
from fastapi.responses import Response

from magicdb.collection import service

STICKY_COOKIE = "magicdb_add"
ONE_YEAR = 365 * 24 * 60 * 60
DEFAULTS = {"finish": "nonfoil"}


def read_sticky(request: Request, offered: tuple[str, ...] = service.FINISHES) -> dict[str, str]:
    """The finish of the last copies added from a card's page, if this form offers it;
    else the first one it offers."""
    stored = parse_qs(request.cookies.get(STICKY_COOKIE, ""))
    finish = (stored.get("finish") or [""])[0]
    if finish not in service.FINISHES:
        finish = DEFAULTS["finish"]
    return {"finish": finish if finish in offered else offered[0]}


def write_sticky(response: Response, finish: object) -> None:
    value = urlencode({"finish": str(finish or "")})
    response.set_cookie(STICKY_COOKIE, value, max_age=ONE_YEAR, samesite="lax", path="/")


def made_in(finishes: list[str]) -> tuple[str, ...]:
    """The finishes a printing was made in, in the usual order; every finish when the
    catalog does not say."""
    return tuple(f for f in service.FINISHES if f in finishes) or service.FINISHES


def options(finishes: tuple[str, ...] = service.FINISHES) -> dict[str, object]:
    return {"finishes": finishes}
