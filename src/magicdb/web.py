"""Browsing pages: home, search results, card detail, syntax help (SPEC §7)."""

import json
import re
import sqlite3
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from magicdb import db, search
from magicdb.catalog.ingest import normalize
from magicdb.config import LANGS
from magicdb.search import panel
from magicdb.search.compiler import LIKE, ORDERS, _contains
from magicdb.templating import LANG_COOKIE, ONE_YEAR, safe_next

router = APIRouter()
Conn = Annotated[sqlite3.Connection, Depends(db.get_conn)]

VIEWS = ("grid", "list")
AUTOCOMPLETE_LIMIT = 15
_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def render(request: Request, template: str, status: int = 200, **context: object) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request, template, context, status_code=status
    )


def no_catalog(request: Request) -> HTMLResponse:
    return render(request, "no_catalog.html", 503)


def panel_options(conn: sqlite3.Connection) -> dict[str, list]:
    """Choices for the filter panel's pickers."""
    facets: dict[str, list[str]] = {}
    for row in conn.execute("SELECT kind, value FROM cat.facets ORDER BY kind, value"):
        facets.setdefault(row["kind"], []).append(row["value"])
    return {
        "types": sorted(set(facets.get("type", [])) | set(facets.get("supertype", []))),
        "subtypes": facets.get("subtype", []),
        "keywords": facets.get("keyword", []),
        "formats": facets.get("format", []),
        "sets": conn.execute(
            "SELECT code, name FROM cat.sets WHERE catalog_count > 0 "
            "ORDER BY released_at DESC, code"
        ).fetchall(),
        "locations": [r["name"] for r in conn.execute("SELECT name FROM locations ORDER BY name")],
        "orders": list(ORDERS),
    }


@router.get("/", response_class=HTMLResponse)
def home(request: Request, conn: Conn) -> HTMLResponse:
    if not db.has_catalog(conn):
        return render(request, "home.html", catalog=None, options=None)
    meta = dict(conn.execute("SELECT key, value FROM cat.meta"))
    catalog = {"count": int(meta["row_count"]), "date": meta["built_at"][:10]}
    return render(request, "home.html", catalog=catalog, options=panel_options(conn))


@router.get("/filter")
def filter_panel(request: Request) -> RedirectResponse:
    """The panel's form lands here; it becomes a search string and a redirect."""
    params = request.query_params
    query = panel.build_query({key: params.getlist(key) for key in params})
    return RedirectResponse("/search?" + urlencode({"q": query}), status_code=303)


@router.get("/search", response_class=HTMLResponse)
def search_page(
    request: Request, conn: Conn, q: str = "", page: str = "1", view: str = "grid"
) -> HTMLResponse:
    if not db.has_catalog(conn):
        return no_catalog(request)
    view = view if view in VIEWS else "grid"
    page_number = int(page) if page.isascii() and page.isdigit() and len(page) < 7 else 1
    result, error = None, None
    try:
        result = search.run(conn, q, page_number, currency=request.app.state.settings.currency)
    except search.QueryError as exc:
        error = str(exc)
    partial = request.headers.get("HX-Request") == "true"
    return render(
        request,
        "_results.html" if partial else "search.html",
        q=q,
        view=view,
        result=result,
        error=error,
    )


@router.get("/card/{card_id}", response_class=HTMLResponse)
def card_page(request: Request, conn: Conn, card_id: str) -> HTMLResponse:
    if not db.has_catalog(conn):
        return no_catalog(request)
    card = None
    if _ID.fullmatch(card_id):
        card = conn.execute("SELECT * FROM cat.printings WHERE id = ?", (card_id,)).fetchone()
    if card is None:
        return render(request, "not_found.html", 404)
    printings = conn.execute(
        "SELECT id, set_code, set_name, collector_number, rarity, released_at, finishes, "
        "is_digital, price_eur, price_eur_foil, price_usd, price_usd_foil "
        "FROM cat.printings WHERE oracle_id = ? "
        "ORDER BY released_at DESC, set_code, collector_sort, id",
        (card["oracle_id"],),
    ).fetchall()
    return render(
        request,
        "card.html",
        card=card,
        faces=json.loads(card["faces_json"]) if card["faces_json"] else [],
        legal=[f for f in card["legal_in"].strip("|").split("|") if f],
        finishes=[f for f in card["finishes"].strip("|").split("|") if f],
        printings=printings,
    )


@router.get("/lang/{code}")
def set_language(code: str, next: str = "/") -> RedirectResponse:
    """Remember the visitor's interface language and send them back where they were."""
    response = RedirectResponse(safe_next(next), status_code=303)
    if code in LANGS:
        response.set_cookie(LANG_COOKIE, code, max_age=ONE_YEAR, samesite="lax", path="/")
    return response


@router.get("/syntax", response_class=HTMLResponse)
def syntax_page(request: Request) -> HTMLResponse:
    return render(request, "syntax.html", filters=search.FILTERS)


@router.get("/api/autocomplete")
def autocomplete(conn: Conn, q: str = "") -> list[str]:
    """Up to 15 card names containing ``q``; names starting with it come first."""
    text = normalize(q.strip())
    if len(text) < 2 or not db.has_catalog(conn):
        return []
    rows = conn.execute(
        f"SELECT name FROM cat.names WHERE name_norm {LIKE} "
        f"ORDER BY name_norm {LIKE} DESC, LENGTH(name), name LIMIT ?",
        (_contains(text), _contains(text)[1:], AUTOCOMPLETE_LIMIT),
    )
    return [row["name"] for row in rows]


def not_found(request: Request, exc: Exception) -> Response:
    return render(request, "not_found.html", 404)
