"""Collection pages: adding copies, editing them, fast input, statistics (SPEC §7).

Routes stay thin: read the form, call ``service``, render or redirect. Every change is
a POST followed by a redirect, so reloading a page never repeats it.
"""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from markupsafe import escape

from magicdb import db, stats, web
from magicdb.collection import forms, service
from magicdb.collection.service import CollectionError
from magicdb.templating import safe_next

router = APIRouter()
Field = Annotated[str, Form()]


def redirect(target: str) -> RedirectResponse:
    return RedirectResponse(target, status_code=303)


def currency(request: Request) -> str:
    return request.app.state.settings.currency


def as_id(value: str) -> int:
    """A path id; anything that is not a small number matches nothing."""
    return int(value) if value.isascii() and value.isdigit() and len(value) < 10 else -1


# --- adding copies --------------------------------------------------------------------


@router.get("/add/names", response_class=HTMLResponse)
def add_names(conn: web.Conn, name: str = "") -> str:
    """Options for a card-name field's suggestions (the deck page's add form)."""
    return "".join(f'<option value="{escape(n)}">' for n in web.autocomplete(conn, name))


@router.post("/add")
def add(
    request: Request,
    conn: web.Conn,
    printing: Field = "",
    quantity: Field = "1",
    finish: Field = "nonfoil",
    next: Field = "",
) -> Response:
    """The card page's add form: copies of a printing that is in the catalog."""
    try:
        service.add_entry(conn, printing, quantity, finish=finish)
    except CollectionError as error:
        return web.card_view(request, conn, printing, error)
    response = redirect(safe_next(next, f"/card/{printing}#add"))
    forms.write_sticky(response, finish)
    return response


# --- fast input -----------------------------------------------------------------------

FAST_FINISHES = ("nonfoil", "foil")


@router.post("/fast/toggle")
def fast_toggle(
    request: Request,
    conn: web.Conn,
    printing: Field = "",
    finish: Field = "nonfoil",
    action: Field = "add",
    next: Field = "",
) -> Response:
    """Tick a box (one copy, if there is none yet) or untick it (take one back), and show
    the box and the badge again. A box never adds a second copy: a second tab must not
    record one the row would not show."""
    card = None
    if web.is_card_id(printing) and db.has_catalog(conn):
        card = conn.execute("SELECT * FROM cat.printings WHERE id = ?", (printing,)).fetchone()
    if card is None or finish not in FAST_FINISHES or action not in ("add", "remove"):
        return web.render(request, "not_found.html", 404)
    if action == "add":
        if not service.copies_by_finish(conn, [printing]).get((printing, finish)):
            service.add_entry(conn, printing, 1, finish=finish)
    else:
        service.take_one(conn, printing, finish=finish)
    if request.headers.get("HX-Request") != "true":
        return redirect(safe_next(next, "/"))
    return web.render(
        request,
        "_fast_toggled.html",
        card=card,
        finish=finish,
        fast_counts=service.copies_by_finish(conn, [printing]),
        owned=service.owned_counts(conn, [printing], "prints"),
    )


# --- copies: edit, remove (from the card page) ------------------------------------------


def entry_page(
    request: Request,
    conn: sqlite3.Connection,
    entry_id: int,
    next: str,
    error: CollectionError | None = None,
) -> Response:
    target = safe_next(next, "/stats")
    entry = service.get_entry(conn, entry_id)
    if entry is None:
        return redirect(target) if error else web.render(request, "not_found.html", 404)
    return web.render(
        request,
        "entry.html",
        400 if error else 200,
        entry=entry,
        next=target,
        error=error,
        options=forms.options(),
    )


@router.get("/collection/{entry_id}/edit", response_class=HTMLResponse)
def edit_entry(request: Request, conn: web.Conn, entry_id: str, next: str = "") -> Response:
    return entry_page(request, conn, as_id(entry_id), next)


@router.post("/collection/{entry_id}")
def save_entry(
    request: Request,
    conn: web.Conn,
    entry_id: str,
    quantity: Field = "",
    finish: Field = "",
    notes: Field = "",
    purchase_price: Field = "",
    next: Field = "",
) -> Response:
    try:
        service.update_entry(
            conn,
            as_id(entry_id),
            quantity=quantity,
            finish=finish,
            notes=notes,
            purchase_price=purchase_price,
        )
    except CollectionError as error:
        return entry_page(request, conn, as_id(entry_id), next, error)
    return redirect(safe_next(next, "/stats"))


@router.post("/collection/{entry_id}/remove")
def remove_entry(
    request: Request, conn: web.Conn, entry_id: str, quantity: Field = "", next: Field = ""
) -> Response:
    try:
        service.remove_entry(conn, as_id(entry_id), quantity)
    except CollectionError as error:
        return entry_page(request, conn, as_id(entry_id), next, error)
    return redirect(safe_next(next, "/stats"))


# --- statistics -----------------------------------------------------------------------


@router.get("/stats", response_class=HTMLResponse)
def stats_page(
    request: Request, conn: web.Conn, sets: str = "percent", set_type: str = ""
) -> HTMLResponse:
    if not db.has_catalog(conn):
        return web.no_catalog(request)
    sets_sort = sets if sets in stats.SET_SORTS else "percent"
    every = stats.set_completion(conn, sets_sort)  # two queries: done once, then filtered
    kinds = stats.set_types(every)
    set_type = set_type if set_type in kinds else ""
    rows = stats.collect(conn, currency(request))
    return web.render(
        request,
        "stats.html",
        totals=stats.totals(rows),
        charts=stats.breakdowns(rows),
        sets=[s for s in every if not set_type or s.set_type == set_type],
        sets_sort=sets_sort,
        set_type=set_type,
        set_types=kinds,
        missing=stats.missing_query,
        valuable=stats.most_valuable(rows),
        recent=stats.recently_added(rows),
    )
