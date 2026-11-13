"""Collection pages: quick add, the collection, locations (SPEC §7).

Routes stay thin: read the form, call ``service``, render or redirect. Every change is
a POST followed by a redirect, so reloading a page never repeats it.
"""

import sqlite3
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from markupsafe import escape

from magicdb import db, stats, web
from magicdb.collection import forms, service
from magicdb.collection.service import CollectionError
from magicdb.search.compiler import exact_name
from magicdb.templating import safe_next, search_url

router = APIRouter()
Field = Annotated[str, Form()]

MAX_PRINTINGS = 300


def redirect(target: str) -> RedirectResponse:
    return RedirectResponse(target, status_code=303)


def currency(request: Request) -> str:
    return request.app.state.settings.currency


def as_id(value: str) -> int:
    """A path id; anything that is not a small number matches nothing."""
    return int(value) if value.isascii() and value.isdigit() and len(value) < 10 else -1


# --- quick add ------------------------------------------------------------------------


def printings_named(conn: sqlite3.Connection, name: str) -> list[sqlite3.Row]:
    """Every printing of the card with exactly this name; paper before digital."""
    if not name.strip() or not db.has_catalog(conn):
        return []
    where, params = exact_name(name.strip())
    return conn.execute(
        f"SELECT p.id, p.name, p.set_code, p.set_name, p.collector_number, p.released_at, "
        f"p.is_digital, p.pref FROM cat.printings p WHERE {where} "
        f"ORDER BY p.is_digital, p.released_at DESC, p.set_code, p.collector_sort, p.id LIMIT ?",
        (*params, MAX_PRINTINGS),
    ).fetchall()


def pick(printings: list[sqlite3.Row], wanted: str, sticky_set: str) -> str:
    """Which printing to preselect: the one asked for, else one from the set used last,
    else the catalog's preferred one."""
    if not printings:
        return ""
    ids = [p["id"] for p in printings]
    if wanted in ids:
        return wanted
    for p in printings:
        if sticky_set and p["set_code"] == sticky_set:
            return p["id"]
    return max(printings, key=lambda p: p["pref"])["id"]


def add_context(
    request: Request, conn: sqlite3.Connection, name: str, printing: str = ""
) -> dict[str, object]:
    sticky = forms.read_sticky(request)
    printings = printings_named(conn, name)
    suggestions = [] if printings or not name.strip() else web.autocomplete(conn, name)
    return {
        "name": name,
        "printings": printings,
        "selected": pick(printings, printing, sticky["set"]),
        "suggestions": suggestions,
        "sticky": sticky,
        "options": forms.options(conn),
    }


@router.get("/add", response_class=HTMLResponse)
def add_page(
    request: Request,
    conn: web.Conn,
    name: str = "",
    printing: str = "",
    added: str = "",
    n: str = "",
    warn: str = "",
) -> HTMLResponse:
    if not db.has_catalog(conn):
        return web.no_catalog(request)
    last = service.get_entry(conn, as_id(added)) if added else None
    return web.render(
        request,
        "add.html",
        **add_context(request, conn, name, printing),
        last=last,
        n=n if n.isascii() and n.isdigit() else "1",
        warn=warn == "finish",
        error=None,
    )


@router.get("/add/names", response_class=HTMLResponse)
def add_names(conn: web.Conn, name: str = "") -> str:
    """Options for the name field's suggestions."""
    return "".join(f'<option value="{escape(n)}">' for n in web.autocomplete(conn, name))


@router.get("/add/printings", response_class=HTMLResponse)
def add_printings(request: Request, conn: web.Conn, name: str = "") -> HTMLResponse:
    return web.render(request, "_add_printings.html", **add_context(request, conn, name))


@router.get("/add/preview", response_class=HTMLResponse)
def add_preview(request: Request, printing: str = "") -> HTMLResponse:
    selected = printing if web.is_card_id(printing) else ""
    return web.render(request, "_add_preview.html", selected=selected)


@router.post("/add")
def add(
    request: Request,
    conn: web.Conn,
    printing: Field = "",
    name: Field = "",
    quantity: Field = "1",
    finish: Field = "nonfoil",
    condition: Field = "NM",
    language: Field = "en",
    location: Field = "",
    next: Field = "",
) -> Response:
    if not printing:  # Enter pressed before choosing a printing: show the choices
        return web.render(
            request,
            "add.html",
            **add_context(request, conn, name),
            last=None,
            n="1",
            warn=False,
            error=None,
        )
    try:
        place = forms.location_id(location)
        added = service.add_entry(
            conn,
            printing,
            quantity,
            finish=finish,
            condition=condition,
            language=language,
            location_id=place,
        )
    except CollectionError as error:
        return web.render(
            request,
            "add.html",
            400,
            **add_context(request, conn, name, printing),
            last=None,
            n="1",
            warn=False,
            error=error,
        )
    entry = service.get_entry(conn, added.entry_id)
    done = {"added": added.entry_id, "n": int(quantity)}
    if added.unusual_finish:
        done["warn"] = "finish"
    response = redirect(safe_next(next, "") or "/add?" + urlencode(done))
    forms.write_sticky(
        response,
        finish=finish,
        condition=condition,
        language=language,
        location=place,
        set=entry["set_code"],
    )
    return response


# --- fast input -----------------------------------------------------------------------

FAST_FINISHES = ("nonfoil", "foil")
_MAIN_SETS = ("expansion", "core", "masters", "draft_innovation", "commander")


@router.get("/fast")
def fast_start(conn: web.Conn) -> Response:
    """Open fast input on the newest set that is out, in collector-number order."""
    code = None
    if db.has_catalog(conn):
        marks = ", ".join("?" * len(_MAIN_SETS))
        row = conn.execute(
            f"SELECT code FROM cat.sets WHERE catalog_count > 0 AND is_digital = 0 "
            f"AND set_type IN ({marks}) AND released_at <= date('now') "
            f"ORDER BY released_at DESC, code LIMIT 1",
            _MAIN_SETS,
        ).fetchone()
        code = row["code"] if row else None
    query = f"set:{code} order:set" if code else ""
    return redirect(search_url(query, 1, "input"))


@router.get("/fast/settings")
def fast_settings(
    request: Request, condition: str = "", language: str = "", location: str = "", next: str = ""
) -> Response:
    """Remember what fast input gives to new copies (the same memory quick add uses)."""
    sticky = forms.read_sticky(request)
    response = redirect(safe_next(next, "/fast"))
    forms.write_sticky(
        response,
        finish=sticky["finish"],
        condition=condition if condition in service.CONDITIONS else sticky["condition"],
        language=language if language in service.LANGUAGES else sticky["language"],
        location=location if location.isascii() and location.isdigit() else "",
        set=sticky["set"],
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
        return redirect(safe_next(next, "/fast"))
    return web.render(
        request,
        "_fast_toggled.html",
        card=card,
        finish=finish,
        fast_counts=service.copies_like(conn, [printing], **key),
        owned=service.owned_counts(conn, [printing], "prints"),
    )


# --- the collection -------------------------------------------------------------------


@router.get("/collection", response_class=HTMLResponse)
def collection_page(
    request: Request,
    conn: web.Conn,
    q: str = "",
    location: str = "",
    sort: str = "added",
    page: str = "1",
) -> HTMLResponse:
    page_number = int(page) if page.isascii() and page.isdigit() and len(page) < 7 else 1
    page_number = max(1, page_number)
    sort = sort if sort in service.SORTS else "added"
    try:
        place = forms.location_id(location)
        where = service.UNASSIGNED if location == service.UNASSIGNED else place or service.ALL
    except CollectionError:
        location, where = "", service.ALL
    rows, totals = service.list_entries(
        conn, location=where, text=q, sort=sort, page=page_number, currency=currency(request)
    )

    def url(number: int) -> str:
        params = {"q": q, "location": location, "sort": sort, "page": number}
        return "/collection?" + urlencode({k: v for k, v in params.items() if v not in ("", 1)})

    return web.render(
        request,
        "collection.html",
        rows=rows,
        totals=totals,
        everything=service.totals(conn, currency(request)),
        q=q,
        location=location,
        sort=sort,
        page=page_number,
        pages=max(1, -(-totals["entries"] // service.PER_PAGE)),
        page_url=url,
        sort_url=lambda key: (
            "/collection?"
            + urlencode({k: v for k, v in {"q": q, "location": location, "sort": key}.items() if v})
        ),
        options=forms.options(conn),
    )


def entry_page(
    request: Request,
    conn: sqlite3.Connection,
    entry_id: int,
    next: str,
    error: CollectionError | None = None,
) -> Response:
    target = safe_next(next, "/collection")
    entry = service.get_entry(conn, entry_id)
    if entry is None:
        return redirect(target) if error else web.render(request, "not_found.html", 404)
    partial = request.headers.get("HX-Request") == "true" and error is None
    return web.render(
        request,
        "_entry_edit.html" if partial else "entry.html",
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
    return redirect(safe_next(next, "/collection"))


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
    return redirect(safe_next(next, "/collection"))


@router.post("/collection/{entry_id}/remove")
def remove_entry(
    request: Request, conn: web.Conn, entry_id: str, quantity: Field = "", next: Field = ""
) -> Response:
    try:
        service.remove_entry(conn, as_id(entry_id), quantity)
    except CollectionError as error:
        return entry_page(request, conn, as_id(entry_id), next, error)
    return redirect(safe_next(next, "/collection"))


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
