"""Deck pages (SPEC §14). Thin: read the form, call the service, render or redirect."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from magicdb import db, web
from magicdb.collection.routes import as_id, currency, redirect
from magicdb.collection.service import CollectionError
from magicdb.decks import service
from magicdb.decks.service import ImportReport
from magicdb.templating import safe_next

router = APIRouter()
Field = Annotated[str, Form()]


def decks_page(
    request: Request, conn: sqlite3.Connection, error: CollectionError | None = None
) -> HTMLResponse:
    return web.render(
        request,
        "decks.html",
        400 if error else 200,
        decks=service.list_decks(conn),
        shortages=service.shortages(conn),
        error=error,
    )


@router.get("/decks", response_class=HTMLResponse)
def decks(request: Request, conn: web.Conn) -> HTMLResponse:
    return decks_page(request, conn)


@router.post("/decks")
def create_deck(
    request: Request, conn: web.Conn, name: Field = "", format: Field = "", notes: Field = ""
) -> Response:
    try:
        deck_id = service.create_deck(conn, name, format, notes)
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
) -> HTMLResponse:
    try:
        summary = service.summary(conn, deck_id)
        rows = service.deck_rows(conn, deck_id, currency(request))
    except CollectionError:
        return web.render(request, "not_found.html", 404)
    missing_cost = sum(row.missing * row.price for row in rows if row.missing and row.price)
    unpriced = sum(row.missing for row in rows if row.missing and row.price is None)
    suggestions = web.autocomplete(conn, typed) if error and typed else []
    return web.render(
        request,
        "deck.html",
        400 if error else 200,
        deck=summary,
        boards=[(board, [row for row in rows if row.board == board]) for board in service.BOARDS],
        missing_cost=missing_cost,
        unpriced=unpriced,
        has_catalog=db.has_catalog(conn),
        error=error,
        report=report,
        typed=typed,
        suggestions=suggestions,
        added=added,
    )


@router.get("/decks/{deck_id}", response_class=HTMLResponse)
def deck(request: Request, conn: web.Conn, deck_id: str, added: str = "") -> HTMLResponse:
    return deck_page(request, conn, as_id(deck_id), added=added[:200])


@router.get("/decks/{deck_id}/stats", response_class=HTMLResponse)
def deck_statistics(request: Request, conn: web.Conn, deck_id: str) -> HTMLResponse:
    try:
        summary = service.summary(conn, as_id(deck_id))
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
    format: Field = "",
    notes: Field = "",
) -> Response:
    try:
        service.update_deck(conn, as_id(deck_id), name, format, notes)
    except CollectionError as error:
        return deck_page(request, conn, as_id(deck_id), error)
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
    board: Field = "main",
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
            board=board,
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
    board: Field = "main",
    next: Field = "",
) -> Response:
    """The card page's form: the deck is one of its fields, not part of the address."""
    return add_card(request, conn, deck, "", printing, quantity, board, next)


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
    board: Field = "main",
) -> Response:
    number = as_id(deck_id)
    try:
        service.update_card(conn, number, as_id(card_id), quantity, board)
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
