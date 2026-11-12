"""Import and export pages (SPEC §8).

Import is two steps. The upload is parsed and matched against the catalog, and a
preview is shown: nothing is written. Confirming takes a backup, then writes everything
in one transaction. Between the two steps the uploaded file waits in the data
directory's ``tmp`` folder under a random name.
"""

import re
import sqlite3
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from magicdb import db, i18n, web
from magicdb.collection import exporters, forms, importers, service
from magicdb.collection.importers import Defaults, FileProblem, Parsed
from magicdb.collection.importers.profiles import Row
from magicdb.collection.service import CollectionError
from magicdb.config import Settings
from magicdb.decks import service as decks
from magicdb.templating import resolve_language

router = APIRouter()
Field = Annotated[str, Form()]

MAX_UPLOAD = 20 * 1024 * 1024
KEEP_SECONDS = 24 * 60 * 60
SHOWN_PROBLEMS = 200
SHOWN_MATCHES = 15
_TOKEN = re.compile(r"[0-9a-f]{32}")
_BINDER_FILE = re.compile(r"binder-(.+)-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\.csv", re.I)


def settings_of(request: Request) -> Settings:
    return request.app.state.settings


def upload_path(settings: Settings, token: str) -> Path | None:
    """Where an upload waits between preview and confirm; None for a bad token."""
    return settings.tmp_dir / f"import-{token}.upload" if _TOKEN.fullmatch(token) else None


def forget_old_uploads(settings: Settings) -> None:
    for path in settings.tmp_dir.glob("import-*.upload"):
        try:
            if time.time() - path.stat().st_mtime > KEEP_SECONDS:
                path.unlink()
        except OSError:
            continue


def suggested_location(filename: str) -> str:
    """TopDecked names its files binder-<Binder name>-<id>.csv: offer the binder's name."""
    match = _BINDER_FILE.fullmatch(filename)
    return match.group(1).replace("_", " ").strip()[: service.MAX_NAME] if match else ""


def defaults_from(
    conn: sqlite3.Connection,
    location: str,
    new_location: str,
    new_kind: str,
    condition: str,
    language: str,
) -> Defaults:
    """What the confirm form chose for rows that do not say for themselves."""
    name, kind = "", "other"
    if location == "new":
        name = " ".join(new_location.split())
        kind = new_kind if new_kind in service.KINDS else "other"
        if not name or len(name) > service.MAX_NAME:
            raise CollectionError("bad_location_name")
    elif location:
        row = conn.execute(
            "SELECT name FROM locations WHERE id = ?", (forms.location_id(location),)
        ).fetchone()
        if row is None:
            raise CollectionError("unknown_location")
        name = row["name"]
    return Defaults(
        condition=condition if condition in service.CONDITIONS else "NM",
        language=language if language in service.LANGUAGES else "en",
        location=name,
        location_kind=kind,
    )


def load(conn: sqlite3.Connection, settings: Settings, token: str, defaults: Defaults) -> Parsed:
    path = upload_path(settings, token)
    if path is None or not path.exists():
        raise FileProblem("expired")
    parsed = importers.parse_file(path.read_bytes())
    importers.resolve(conn, parsed, defaults)
    return parsed


def describe(request: Request) -> Callable[[Row], str]:
    """A function giving, in the visitor's language, what is wrong with a row."""
    t = i18n.translator(resolve_language(request, settings_of(request).lang))
    return lambda row: t("import.problem." + row.problem, **row.detail)


# --- import ---------------------------------------------------------------------------


def upload_page(request: Request, error: str = "", status: int = 200) -> HTMLResponse:
    return web.render(request, "import.html", status, error=error)


@router.get("/import", response_class=HTMLResponse)
def import_page(request: Request, conn: web.Conn, error: str = "") -> HTMLResponse:
    if not db.has_catalog(conn):
        return web.no_catalog(request)
    return upload_page(request, error if error == "expired" else "")


def preview_page(
    request: Request,
    conn: sqlite3.Connection,
    token: str,
    parsed: Parsed,
    filename: str,
    chosen: dict[str, str],
    error: CollectionError | None = None,
) -> HTMLResponse:
    problems = [row for row in parsed.rows if row.status != "matched"]
    matched = [row for row in parsed.rows if row.status == "matched"]
    return web.render(
        request,
        "import_preview.html",
        400 if error else 200,
        token=token,
        filename=filename,
        profile=parsed.profile,
        summary=importers.summary(parsed),
        problems=problems[:SHOWN_PROBLEMS],
        hidden_problems=max(0, len(problems) - SHOWN_PROBLEMS),
        sample=matched[:SHOWN_MATCHES],
        without={
            "location": sum(not row.location.strip() for row in matched),
            "condition": sum(not row.condition.strip() for row in matched),
            "language": sum(not row.language.strip() for row in matched),
        },
        chosen=chosen,
        owned=service.totals(conn)["entries"],
        options=forms.options(conn),
        error=error,
    )


@router.post("/import")
def upload(request: Request, conn: web.Conn, file: Annotated[UploadFile, File()]) -> Response:
    settings = settings_of(request)
    data = file.file.read(MAX_UPLOAD + 1)
    filename = Path(file.filename or "").name[:200]
    if len(data) > MAX_UPLOAD:
        return upload_page(request, "too_big", 400)
    try:
        if not db.has_catalog(conn):
            raise FileProblem("no_catalog")
        parsed = importers.parse_file(data)
        importers.resolve(conn, parsed)
    except FileProblem as problem:
        return upload_page(request, problem.code, 400)
    token = uuid.uuid4().hex
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    forget_old_uploads(settings)
    upload_path(settings, token).write_bytes(data)
    suggestion = suggested_location(filename)
    chosen = {
        "location": "new" if suggestion else "",
        "new_location": suggestion,
        "new_kind": "binder",
        "condition": "NM",
        "language": "en",
        "mode": "add",
    }
    return preview_page(request, conn, token, parsed, filename, chosen)


@router.post("/import/{token}/confirm")
def confirm(
    request: Request,
    conn: web.Conn,
    token: str,
    filename: Field = "",
    location: Field = "",
    new_location: Field = "",
    new_kind: Field = "binder",
    condition: Field = "NM",
    language: Field = "en",
    mode: Field = "add",
) -> Response:
    settings = settings_of(request)
    chosen = {
        "location": location,
        "new_location": new_location,
        "new_kind": new_kind,
        "condition": condition,
        "language": language,
        "mode": mode,
    }
    try:
        parsed = load(conn, settings, token, Defaults())
    except FileProblem as problem:
        return RedirectResponse("/import?" + urlencode({"error": problem.code}), status_code=303)
    try:
        defaults = defaults_from(conn, location, new_location, new_kind, condition, language)
        importers.resolve(conn, parsed, defaults)
        db.backup_collection(conn, settings, "pre-import")
        result = service.import_entries(
            conn,
            importers.to_items(parsed),
            replace=mode == "replace",
            locations=parsed.locations,
            source=f"{parsed.profile}: {filename}"[:200],
        )
        decks.restore(conn, parsed.decks)
    except CollectionError as error:
        return preview_page(request, conn, token, parsed, filename, chosen, error)
    counts = importers.summary(parsed)
    done = {
        "entries": result["entries"],
        "copies": result["copies"],
        "skipped": counts["ambiguous"] + counts["failed"],
    }
    return RedirectResponse(f"/import/{token}/done?" + urlencode(done), status_code=303)


@router.get("/import/{token}/done", response_class=HTMLResponse)
def done(
    request: Request, token: str, entries: str = "0", copies: str = "0", skipped: str = "0"
) -> HTMLResponse:
    def number(value: str) -> int:
        return int(value) if value.isascii() and value.isdigit() and len(value) < 9 else 0

    if upload_path(settings_of(request), token) is None:
        return web.render(request, "not_found.html", 404)
    return web.render(
        request,
        "import_done.html",
        token=token,
        entries=number(entries),
        copies=number(copies),
        skipped=number(skipped),
    )


@router.get("/import/{token}/unresolved.csv")
def unresolved(request: Request, conn: web.Conn, token: str) -> Response:
    try:
        parsed = load(conn, settings_of(request), token, Defaults())
    except FileProblem:
        return web.render(request, "not_found.html", 404)
    return download(
        importers.unresolved_csv(parsed, describe(request)), "text/csv", "magic-db-not-imported.csv"
    )


# --- export ---------------------------------------------------------------------------


def download(text: str, media_type: str, filename: str) -> Response:
    body = ("﻿" + text if media_type == "text/csv" else text).encode("utf-8")
    return Response(
        body,
        media_type=f"{media_type}; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/export", response_class=HTMLResponse)
def export_page(request: Request, conn: web.Conn) -> HTMLResponse:
    return web.render(request, "export.html", totals=service.totals(conn))


@router.get("/export/csv")
def export_csv(conn: web.Conn) -> Response:
    name = f"magic-db-collection-{datetime.now():%Y%m%d}.csv"
    return download(exporters.export_csv(conn), "text/csv", name)


@router.get("/export/json")
def export_json(conn: web.Conn) -> Response:
    name = f"magic-db-collection-{datetime.now():%Y%m%d}.json"
    return download(exporters.export_json(conn), "application/json", name)
