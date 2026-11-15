"""Browsing pages: home, search results, card detail, syntax help (SPEC §7)."""

import json
import re
import sqlite3
from contextlib import closing
from datetime import date
from itertools import groupby
from typing import Annotated
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from markupsafe import Markup, escape

from magicdb import db, search
from magicdb.catalog import ingest
from magicdb.catalog.ingest import normalize
from magicdb.collection import forms, service
from magicdb.config import LANGS, Settings
from magicdb.decks import mode
from magicdb.decks import service as decks
from magicdb.search import panel
from magicdb.search.compiler import LIKE, ORDERS, FilterDef, _contains
from magicdb.templating import (
    LANG_COOKIE,
    ONE_YEAR,
    THEME_COOKIE,
    THEMES,
    safe_next,
    search_url,
)

router = APIRouter()
Conn = Annotated[sqlite3.Connection, Depends(db.get_conn)]

# The search results are shown as images or as a list (``view=`` in the address). What
# the controls on them do — fast input, deck mode or nothing — follows the edit switch
# (lock.py), not the address. Addresses saved when a view also chose the mode keep
# their layout.
LAYOUTS = ("grid", "list")
OLD_VIEWS = {"input": "list", "input-grid": "grid", "deck": "grid", "deck-list": "list"}
SEARCH_MODES = {"input": "input", "deck": "deck"}  # edit switch -> controls on the results
SCOPES = ("all", "collection")  # what the switch beside the search box sends
FAST_PER_PAGE = 500  # fast input: a whole set on one page
AUTOCOMPLETE_LIMIT = 15
SUGGEST_LIMIT = 8
# What the home page shows under the search box. One choice for the whole server, made
# in Administration, kept in the ``settings`` table and mirrored in ``app.state.home``.
HOME_VIEWS = ("map", "filters")
HOME_KEY = "home"
RECENT_YEARS = 5  # how far back the home page's list of expansions goes
today = date.today  # a name of its own, so that a test can choose the day

# The syntax page's first examples, in the order of the strings "syntax.basic.N".
BASICS = (
    "lightning bolt",
    '"serra angel"',
    '!"Lightning Bolt"',
    "t:dragon c:r",
    "t:elf or t:goblin",
    "(t:elf or t:goblin) mv<=2",
    "c:r -t:creature",
)

# The home page's map of the syntax: every filter of the table (``search.FILTERS``, by
# name) under a heading, whose title is the string "map.group.<heading>".
SYNTAX_MAP: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("card", ("name", "oracle", "type", "keyword", "power", "toughness", "loyalty")),
    ("colors", ("color", "identity", "mv", "mana")),
    (
        "printing",
        ("set", "settype", "number", "rarity", "year", "date", "artist", "flavor", "is"),
    ),
    ("market", ("format", "eur", "usd")),
    ("mine", ("have", "haveprint", "qty", "finish", "deck")),
    ("display", ("unique", "order", "dir", "include")),
)
_SYNTAX = re.compile(r'[:=<>()"!]|\bor\b', re.IGNORECASE)
_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def is_card_id(value: str) -> bool:
    return bool(_ID.fullmatch(value))


def url_with(path: str, **params: object) -> str:
    return f"{path}?{urlencode(params)}"


def render(request: Request, template: str, status: int = 200, **context: object) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request, template, context, status_code=status
    )


def swap_in_address(address: str, kind: str, old: str, new: str) -> str:
    """A search address whose search is limited to the deck ``old`` (``kind`` "deck"),
    now limited to ``new``; any other address as it is."""
    parts = urlsplit(address)
    params = parse_qs(parts.query, keep_blank_values=True)
    query = (params.get("q") or [""])[0]
    swapped = search.swap_named(query, kind, old, new)
    if swapped == query:
        return address
    params["q"] = [swapped]
    params.pop("page", None)
    return urlunsplit(parts._replace(query=urlencode(params, doseq=True)))


def settings_redirect(request: Request, target: str, changed: bool) -> Response:
    """Back to the search after the picker above the results changed (deck mode's deck).
    With HTMX only the results are redrawn — unless the search itself was rewritten, which
    the search box must show too: then the whole page loads."""
    if changed and request.headers.get("HX-Request") == "true":
        return Response(status_code=204, headers={"HX-Redirect": target})
    return RedirectResponse(target, status_code=303)


def no_catalog(request: Request) -> HTMLResponse:
    return render(
        request, "no_catalog.html", 503, running=ingest.is_running(), progress=ingest.progress
    )


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
        "orders": list(ORDERS),
    }


def load_home(settings: Settings) -> str:
    """The saved choice; the map of the syntax when nothing (or nonsense) was saved."""
    with closing(db.connect(settings)) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (HOME_KEY,)).fetchone()
    return row["value"] if row is not None and row["value"] in HOME_VIEWS else HOME_VIEWS[0]


def save_home(conn: sqlite3.Connection, view: str) -> None:
    with conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (HOME_KEY, view),
        )


def syntax_map() -> list[tuple[str, list[FilterDef]]]:
    """The filters of the search, in the groups the home page shows them in."""
    by_name = {definition.name: definition for definition in search.FILTERS}
    return [(group, [by_name[name] for name in names]) for group, names in SYNTAX_MAP]


def recent_expansions(conn: sqlite3.Connection, on: date) -> list[tuple[str, list[sqlite3.Row]]]:
    """The expansions of the five years before ``on``, newest first, year by year.

    Only sets printed on paper that have cards in the catalog; one that is announced and
    already has cards is the newest of all, and is listed too.
    """
    rows = conn.execute(
        "SELECT code, name, released_at FROM cat.sets "
        "WHERE set_type = ? AND is_digital = 0 AND catalog_count > 0 "
        "AND released_at >= date(?, ?) ORDER BY released_at DESC, code",
        ("expansion", on.isoformat(), f"-{RECENT_YEARS} years"),
    )
    return [(year, list(sets)) for year, sets in groupby(rows, lambda row: row["released_at"][:4])]


@router.get("/", response_class=HTMLResponse)
def home(request: Request, conn: Conn) -> HTMLResponse:
    if not db.has_catalog(conn):
        return render(request, "home.html", catalog=False, options=None)
    if getattr(request.app.state, "home", HOME_VIEWS[0]) == "filters":
        return render(request, "home.html", catalog=True, options=panel_options(conn))
    return render(
        request,
        "home.html",
        catalog=True,
        options=None,
        basics=BASICS,
        syntax_map=syntax_map(),
        recent=recent_expansions(conn, today()),
        recent_years=RECENT_YEARS,
    )


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
    view = view if view in LAYOUTS else OLD_VIEWS.get(view, "grid")
    layout = view
    mode_name = SEARCH_MODES.get(request.app.state.edit_mode, "")
    page_number = int(page) if page.isascii() and page.isdigit() and len(page) < 7 else 1
    scope = request.query_params.get("scope")
    if scope in SCOPES:
        # the switch beside the search box (and addresses saved when the collection was
        # a place to search, not a term): the choice is written into the search itself
        mine = search.with_collection(q, scope == "collection")
        return RedirectResponse(search_url(mine, page_number, view), status_code=303)
    result, error = None, None
    try:
        result = search.run(
            conn,
            q,
            page_number,
            currency=request.app.state.settings.currency,
            prints=mode_name == "input",
            cards=mode_name == "deck",
            per_page=FAST_PER_PAGE if mode_name == "input" else search.PER_PAGE,
        )
    except search.QueryError as exc:
        error = str(exc)
    owned: dict[str, int] = {}
    if result is not None:
        # a card's badge counts every printing; a printing's badge counts only itself
        key = "id" if result.unique == "prints" else "oracle_id"
        owned = service.owned_counts(conn, [row[key] for row in result.rows], result.unique)
    extra: dict[str, object] = {}
    if result is not None and mode_name == "input":
        extra = fast_input_context(conn, result)
    elif result is not None and mode_name == "deck":
        extra = deck_mode_context(request, conn, q, result, view)
    partial = request.headers.get("HX-Request") == "true"
    return render(
        request,
        "_results.html" if partial else "search.html",
        **extra,
        q=q,
        mine=search.in_collection(q),
        without_collection=search.with_collection(q, False),
        view=view,
        layout=layout,
        mode=mode_name,
        result=result,
        error=error,
        owned=owned,
        sort=search.sort_links(q, result) if result is not None else {},
    )


def only_switch(q: str, view: str, kind: str, name: str | None) -> dict[str, object] | None:
    """The switch beside deck mode's picker: whether the search is limited to that deck,
    and the search that turns it the other way. None when there is no deck."""
    if not name:
        return None
    on = search.has_named(q, kind, name)
    return {"on": on, "url": search_url(search.with_named(q, kind, name, not on), 1, view)}


def fast_input_context(conn: sqlite3.Connection, result: search.Result) -> dict[str, object]:
    return {"fast_counts": service.copies_by_finish(conn, [row["id"] for row in result.rows])}


def deck_mode_context(
    request: Request, conn: sqlite3.Connection, q: str, result: search.Result, view: str
) -> dict[str, object]:
    chosen = mode.choice(request, conn)
    counts: dict[str, int] = {}
    if chosen.deck is not None:
        ids = [row["oracle_id"] for row in result.rows]
        counts = decks.copies_in_deck(conn, chosen.deck["id"], ids)
    return {
        "deck_choice": chosen,
        "deck_counts": counts,
        "only": only_switch(q, view, "deck", chosen.deck["name"] if chosen.deck else None),
    }


@router.get("/search/suggest", response_class=HTMLResponse)
def search_suggestions(conn: Conn, q: str = "") -> str:
    """Names to offer under the search box while typing, as links.

    Only for a plain name: once the text has filter syntax in it there is nothing
    sensible to complete. The collection switch's term is the exception: beside it a
    name is still completed, with names that are in the collection.
    """
    mine = search.in_collection(q)
    text = search.with_collection(q, False).strip()
    if len(text) < 2 or _SYNTAX.search(text):
        return ""
    if mine:
        names = [
            row["name"]
            for row in conn.execute(
                f"SELECT DISTINCT name FROM entries WHERE name {LIKE} "
                f"ORDER BY name {LIKE} DESC, LENGTH(name), name LIMIT ?",
                (_contains(text), _contains(text)[1:], SUGGEST_LIMIT),
            )
        ]
    else:
        names = autocomplete(conn, text)[:SUGGEST_LIMIT]
    rest = f" {search.COLLECTION_TERM}" if mine else ""
    return "".join(
        f'<a role="option" href="{escape(search_url(exact(name) + rest))}">'
        f"{highlight(name, text)}</a>"
        for name in names
    )


def exact(name: str) -> str:
    """The search for exactly this card."""
    return f'!"{name}"'


def highlight(name: str, typed: str) -> Markup:
    """The name with what was typed in normal weight and the rest in bold, as search
    engines show completions."""
    at = name.lower().find(typed.lower())
    if at < 0:
        return Markup(f"<b>{escape(name)}</b>")
    end = at + len(typed)
    return Markup(f"<b>{escape(name[:at])}</b>{escape(name[at:end])}<b>{escape(name[end:])}</b>")


@router.get("/card/{card_id}", response_class=HTMLResponse)
def card_page(request: Request, conn: Conn, card_id: str) -> HTMLResponse:
    return card_view(request, conn, card_id)


def card_view(
    request: Request,
    conn: sqlite3.Connection,
    card_id: str,
    error: service.CollectionError | None = None,
) -> HTMLResponse:
    """The card page; with ``error``, answered 400 with the problem above its add form."""
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
        400 if error else 200,
        card=card,
        faces=json.loads(card["faces_json"]) if card["faces_json"] else [],
        legal=[f for f in card["legal_in"].strip("|").split("|") if f],
        finishes=[f for f in card["finishes"].strip("|").split("|") if f],
        printings=printings,
        copies=service.copies_of_card(conn, card["oracle_id"]),
        in_decks=decks.decks_with_card(conn, card["oracle_id"]),
        # the deck the form at the bottom offers first: the one deck mode fills
        deck_choice=mode.choice(request, conn),
        sticky=forms.read_sticky(request),
        options=forms.options(),
        error=error,
    )


@router.get("/lang/{code}")
def set_language(code: str, next: str = "/") -> RedirectResponse:
    """Remember the visitor's interface language and send them back where they were."""
    response = RedirectResponse(safe_next(next), status_code=303)
    if code in LANGS:
        response.set_cookie(LANG_COOKIE, code, max_age=ONE_YEAR, samesite="lax", path="/")
    return response


@router.post("/theme")
def choose_theme(
    theme: Annotated[str, Form()] = "", next: Annotated[str, Form()] = "/"
) -> RedirectResponse:
    """Light or dark pages in this browser, or what its device asks for ("auto")."""
    response = RedirectResponse(safe_next(next), status_code=303)
    if theme in THEMES:
        response.set_cookie(THEME_COOKIE, theme, max_age=ONE_YEAR, samesite="lax", path="/")
    elif theme == "auto":
        response.delete_cookie(THEME_COOKIE, path="/", samesite="lax")
    return response


@router.get("/syntax", response_class=HTMLResponse)
def syntax_page(request: Request) -> HTMLResponse:
    return render(request, "syntax.html", filters=search.FILTERS, basics=BASICS)


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
