"""Collection pages: adding copies, the collection, locations (SPEC §7).

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
    condition: Field = "NM",
    language: Field = "en",
    location: Field = "",
    next: Field = "",
) -> Response:
    """The card page's add form: copies of a printing that is in the catalog."""
    try:
        place = forms.location_id(location)
        service.add_entry(
            conn,
            printing,
            quantity,
            finish=finish,
            condition=condition,
            language=language,
            location_id=place,
        )
    except CollectionError as error:
        return web.card_view(request, conn, printing, error)
    response = redirect(safe_next(next, f"/card/{printing}#mine"))
    forms.write_sticky(
        response, finish=finish, condition=condition, language=language, location=place
    )
    return response


# --- fast input -----------------------------------------------------------------------

FAST_FINISHES = ("nonfoil", "foil")


@router.get("/fast/settings")
def fast_settings(
    request: Request,
    conn: web.Conn,
    condition: str = "",
    language: str = "",
    location: str = "",
    next: str = "",
) -> Response:
    """Remember what fast input gives to new copies (the same memory the card page's add
    form uses). A search limited to the location chosen before follows the new one."""
    sticky = forms.read_sticky(request)
    names = {str(row["id"]): row["name"] for row in forms.options(conn)["locations"]}
    back = target = safe_next(next, "/")
    old, new = names.get(sticky["location"]), names.get(location)
    if old and new:
        target = web.swap_in_address(back, "loc", old, new)
    response = web.settings_redirect(request, target, changed=target != back)
    forms.write_sticky(
        response,
        finish=sticky["finish"],
        condition=condition if condition in service.CONDITIONS else sticky["condition"],
        language=language if language in service.LANGUAGES else sticky["language"],
        location=location if location.isascii() and location.isdigit() else "",
    )
    return response


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
    key = forms.copy_key(request, conn)
    if action == "add":
        if not service.copies_like(conn, [printing], **key).get((printing, finish)):
            try:
                service.add_entry(conn, printing, 1, finish=finish, **key)
            except CollectionError:
                pass  # e.g. the location went away meanwhile: the row shows what is there
    else:
        service.take_one(conn, printing, finish=finish, **key)
    if request.headers.get("HX-Request") != "true":
        return redirect(safe_next(next, "/"))
    return web.render(
        request,
        "_fast_toggled.html",
        card=card,
        finish=finish,
        fast_counts=service.copies_like(conn, [printing], **key),
        owned=service.owned_counts(conn, [printing], "prints"),
    )


# --- copies: edit, move, remove (from the card page) ------------------------------------


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
        options=forms.options(conn),
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
    condition: Field = "",
    language: Field = "",
    location: Field = "",
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
            condition=condition,
            language=language,
            location_id=forms.location_id(location),
            notes=notes,
            purchase_price=purchase_price,
        )
    except CollectionError as error:
        return entry_page(request, conn, as_id(entry_id), next, error)
    return redirect(safe_next(next, "/stats"))


@router.post("/collection/{entry_id}/move")
def move_entry(
    request: Request,
    conn: web.Conn,
    entry_id: str,
    quantity: Field = "",
    location: Field = "",
    next: Field = "",
) -> Response:
    try:
        service.move_entry(conn, as_id(entry_id), quantity, forms.location_id(location))
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


# --- locations ------------------------------------------------------------------------


def locations_page(
    request: Request, conn: sqlite3.Connection, error: CollectionError | None = None
) -> HTMLResponse:
    money = currency(request)
    return web.render(
        request,
        "locations.html",
        400 if error else 200,
        locations=service.list_locations(conn, money),
        unassigned=service.totals(conn, money, "e.location_id IS NULL"),
        options=forms.options(conn),
        error=error,
    )


@router.get("/locations", response_class=HTMLResponse)
def locations(request: Request, conn: web.Conn) -> HTMLResponse:
    return locations_page(request, conn)


def location_action(request: Request, conn: sqlite3.Connection, action, anchor: str) -> Response:
    try:
        action()
    except CollectionError as error:
        return locations_page(request, conn, error)
    return redirect("/locations" + anchor)


@router.post("/locations")
def create_location(
    request: Request, conn: web.Conn, name: Field = "", kind: Field = "other", notes: Field = ""
) -> Response:
    return location_action(
        request, conn, lambda: service.create_location(conn, name, kind, notes), ""
    )


@router.post("/locations/{location_id}")
def update_location(
    request: Request,
    conn: web.Conn,
    location_id: str,
    name: Field = "",
    kind: Field = "other",
    notes: Field = "",
) -> Response:
    number = as_id(location_id)
    return location_action(
        request,
        conn,
        lambda: service.update_location(conn, number, name, kind, notes),
        f"#l{number}",
    )


@router.post("/locations/{location_id}/shift")
def shift_location(
    request: Request, conn: web.Conn, location_id: str, offset: Field = "1"
) -> Response:
    number = as_id(location_id)
    step = -1 if offset == "-1" else 1
    return location_action(
        request, conn, lambda: service.shift_location(conn, number, step), f"#l{number}"
    )


@router.post("/locations/{location_id}/empty")
def empty_location(
    request: Request, conn: web.Conn, location_id: str, destination: Field = ""
) -> Response:
    number = as_id(location_id)
    return location_action(
        request,
        conn,
        lambda: service.empty_location(conn, number, forms.location_id(destination)),
        f"#l{number}",
    )


@router.post("/locations/{location_id}/delete")
def delete_location(request: Request, conn: web.Conn, location_id: str) -> Response:
    number = as_id(location_id)
    return location_action(request, conn, lambda: service.delete_location(conn, number), "")


# --- statistics -----------------------------------------------------------------------


@router.get("/stats", response_class=HTMLResponse)
def stats_page(
    request: Request, conn: web.Conn, location: str = "", sets: str = "percent", set_type: str = ""
) -> HTMLResponse:
    if not db.has_catalog(conn):
        return web.no_catalog(request)
    try:
        place = forms.location_id(location)
        where = service.UNASSIGNED if location == service.UNASSIGNED else place or service.ALL
    except CollectionError:
        location, place, where = "", None, service.ALL
    place_name = None
    if place is not None:
        found = conn.execute("SELECT name FROM locations WHERE id = ?", (place,)).fetchone()
        place_name = found["name"] if found else None
    sets_sort = sets if sets in stats.SET_SORTS else "percent"
    kinds = stats.set_types(conn, where)
    set_type = set_type if set_type in kinds else ""
    rows = stats.collect(conn, where, currency(request))
    return web.render(
        request,
        "stats.html",
        location=location,
        totals=stats.totals(rows),
        charts=stats.breakdowns(rows),
        sets=stats.set_completion(conn, where, sets_sort, set_type),
        sets_sort=sets_sort,
        set_type=set_type,
        set_types=kinds,
        missing=lambda code: stats.missing_query(code, place_name),
        valuable=stats.most_valuable(rows),
        recent=stats.recently_added(rows),
        options=forms.options(conn),
    )
