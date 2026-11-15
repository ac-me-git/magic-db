"""Deck pages (SPEC §14). Thin: read the form, call the service, render or redirect."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from magicdb import db, i18n, web
from magicdb.collection.routes import as_id, currency, redirect
from magicdb.collection.service import CollectionError
from magicdb.decks import mode, service
from magicdb.decks.service import ImportReport
from magicdb.templating import resolve_language, safe_next

router = APIRouter()
Field = Annotated[str, Form()]


def decks_page(
    request: Request, conn: sqlite3.Connection, error: CollectionError | None = None
) -> HTMLResponse:
    return web.render(
        request,
        "decks.html",
        400 if error else 200,
        decks=service.list_decks(conn, currency(request)),
        shortages=service.shortages(conn),
        error=error,
    )


@router.get("/decks", response_class=HTMLResponse)
def decks(request: Request, conn: web.Conn) -> HTMLResponse:
    return decks_page(request, conn)


@router.post("/decks")
def create_deck(request: Request, conn: web.Conn, name: Field = "", notes: Field = "") -> Response:
    """With no name (the New deck button) the deck is "Deck 1", "Deck 2"... in the
    visitor's language, and its page opens with the name ready to change."""
    if not name.strip():
        t = i18n.translator(resolve_language(request, request.app.state.settings.lang))
        deck_id = service.create_numbered(conn, t("decks.default_name"))
        return redirect(f"/decks/{deck_id}?new=1")
    try:
        deck_id = service.create_deck(conn, name, notes)
    except CollectionError as error:
        return decks_page(request, conn, error)
    return redirect(f"/decks/{deck_id}")


def deck_page(
    request: Request,
    conn: sqlite3.Connection,
    deck_id: int,
    error: CollectionError | None = None,
    report: ImportReport | None = None,
    typed: str = "",
    added: str = "",
    editing: str = "",
) -> HTMLResponse:
    """``editing`` opens the name and notes: "new" for a deck just made (the
    name is selected, ready to type over), "error" when saving them failed."""
    try:
        summary = service.summary(conn, deck_id, currency(request))
        rows = service.deck_rows(conn, deck_id, currency(request))
    except CollectionError:
        return web.render(request, "not_found.html", 404)
    suggestions = web.autocomplete(conn, typed) if error and typed else []
    return web.render(
        request,
        "deck.html",
        400 if error else 200,
        deck=summary,
        rows=rows,
        has_catalog=db.has_catalog(conn),
        error=error,
        report=report,
        typed=typed,
        suggestions=suggestions,
        added=added,
        editing=editing,
    )


@router.get("/decks/{deck_id}", response_class=HTMLResponse)
def deck(
    request: Request, conn: web.Conn, deck_id: str, added: str = "", new: str = ""
) -> HTMLResponse:
    return deck_page(request, conn, as_id(deck_id), added=added[:200], editing="new" if new else "")


@router.get("/decks/{deck_id}/stats", response_class=HTMLResponse)
def deck_statistics(request: Request, conn: web.Conn, deck_id: str) -> HTMLResponse:
    try:
        summary = service.summary(conn, as_id(deck_id), currency(request))
        rows = service.deck_rows(conn, as_id(deck_id), currency(request))
    except CollectionError:
        return web.render(request, "not_found.html", 404)
    return web.render(request, "deck_stats.html", deck=summary, stats=service.deck_stats(rows))


@router.post("/decks/{deck_id}")
def update_deck(
    request: Request,
    conn: web.Conn,
    deck_id: str,
    name: Field = "",
    notes: Field = "",
) -> Response:
    try:
        service.update_deck(conn, as_id(deck_id), name, notes)
    except CollectionError as error:
        return deck_page(request, conn, as_id(deck_id), error, editing="error")
    return redirect(f"/decks/{as_id(deck_id)}")


@router.post("/decks/{deck_id}/delete")
def delete_deck(request: Request, conn: web.Conn, deck_id: str) -> Response:
    try:
        service.delete_deck(conn, as_id(deck_id))
    except CollectionError:
        return web.render(request, "not_found.html", 404)
    return redirect("/decks")


@router.post("/decks/{deck_id}/cards")
def add_card(
    request: Request,
    conn: web.Conn,
    deck_id: str,
    name: Field = "",
    printing: Field = "",
    quantity: Field = "1",
    next: Field = "",
) -> Response:
    """Add a card by name (from the deck page) or by printing (from a card's page)."""
    number = as_id(deck_id)
    try:
        added = service.add_card(
            conn,
            number,
            name=name,
            printing=printing if web.is_card_id(printing) else "",
            quantity=quantity,
        )
    except CollectionError as error:
        return deck_page(request, conn, number, error, typed=name)
    target = safe_next(next, "")
    return RedirectResponse(
        target or web.url_with(f"/decks/{number}", added=added), status_code=303
    )


@router.post("/deck-cards")
def add_card_to(
    request: Request,
    conn: web.Conn,
    deck: Field = "",
    printing: Field = "",
    quantity: Field = "1",
    next: Field = "",
) -> Response:
    """The card page's form: the deck is one of its fields, not part of the address."""
    return add_card(request, conn, deck, "", printing, quantity, next)


# --- deck mode: + and − beside the cards of a search ------------------------------------


@router.get("/deckmode/settings")
def deck_mode_settings(
    request: Request, conn: web.Conn, deck: str = "", next: str = ""
) -> Response:
    """Choose the deck deck mode fills (a cookie only, so allowed while
    locked). A search limited to the deck chosen before follows the new one."""
    before = mode.choice(request, conn)
    chosen = {str(d["id"]): d for d in before.decks}.get(deck, before.deck)
    back = target = safe_next(next, "/")
    if before.deck is not None and chosen is not None:
        target = web.swap_in_address(back, "deck", before.deck["name"], chosen["name"])
    response = web.settings_redirect(request, target, changed=target != back)
    mode.remember(response, chosen["id"] if chosen is not None else None)
    return response


@router.post("/deckmode/card")
def deck_mode_card(
    request: Request,
    conn: web.Conn,
    deck: Field = "",
    printing: Field = "",
    action: Field = "add",
    next: Field = "",
) -> Response:
    """One more copy of a card in a deck, or one less; then the card's
    buttons again (with HTMX) or the search it came from."""
    number = as_id(deck)
    card = None
    if web.is_card_id(printing) and db.has_catalog(conn):
        card = conn.execute("SELECT * FROM cat.printings WHERE id = ?", (printing,)).fetchone()
    if card is None or action not in ("add", "remove"):
        return web.render(request, "not_found.html", 404)
    try:
        if action == "add":
            service.add_card(conn, number, printing=printing)
        else:
            service.take_card(conn, number, printing=printing)
    except CollectionError:
        return web.render(request, "not_found.html", 404)  # the deck went away
    if request.headers.get("HX-Request") != "true":
        return redirect(safe_next(next, "/"))
    return web.render(
        request,
        "_deck_counted.html",
        card=card,
        deck_id=number,
        deck_counts=service.copies_in_deck(conn, number, [card["oracle_id"]]),
    )


@router.post("/decks/{deck_id}/import")
def import_list(request: Request, conn: web.Conn, deck_id: str, text: Field = "") -> Response:
    number = as_id(deck_id)
    try:
        report = service.import_text(conn, number, text[:200_000])
    except CollectionError as error:
        return deck_page(request, conn, number, error)
    return deck_page(request, conn, number, report=report)


@router.post("/decks/{deck_id}/cards/{card_id}")
def update_card(
    request: Request,
    conn: web.Conn,
    deck_id: str,
    card_id: str,
    quantity: Field = "",
) -> Response:
    number = as_id(deck_id)
    try:
        service.update_card(conn, number, as_id(card_id), quantity)
    except CollectionError as error:
        return deck_page(request, conn, number, error)
    return redirect(f"/decks/{number}")


@router.post("/decks/{deck_id}/cards/{card_id}/remove")
def remove_card(request: Request, conn: web.Conn, deck_id: str, card_id: str) -> Response:
    number = as_id(deck_id)
    try:
        service.remove_card(conn, number, as_id(card_id))
    except CollectionError as error:
        return deck_page(request, conn, number, error)
    return redirect(f"/decks/{number}")


@router.get("/decks/{deck_id}/export.txt")
def export_deck(request: Request, conn: web.Conn, deck_id: str) -> Response:
    try:
        listing = service.export_text(conn, as_id(deck_id))
        name = service.get_deck(conn, as_id(deck_id))["name"]
    except CollectionError:
        return web.render(request, "not_found.html", 404)
    filename = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in name).strip() or "deck"
    return Response(
        listing,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}.txt"',
            "Cache-Control": "no-store",
        },
    )
