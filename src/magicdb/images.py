"""Card images: served from a local cache, filled on demand from Scryfall (SPEC §7)."""

import re
import sqlite3
import uuid
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response

from magicdb import db
from magicdb.catalog.ingest import HEADERS
from magicdb.config import Settings

router = APIRouter()

MAX_BYTES = 5 * 1024 * 1024
ALLOWED_HOST = "scryfall.io"
COLUMNS = {
    ("small", "front"): "img_small",
    ("normal", "front"): "img_normal",
    ("small", "back"): "img_back_small",
    ("normal", "back"): "img_back_normal",
}
_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_PLACEHOLDER = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 488 680" role="img" '
    'aria-label="No image">'
    '<rect width="488" height="680" rx="24" fill="#2b2a27"/>'
    '<rect x="28" y="28" width="432" height="624" rx="12" fill="none" stroke="#6b6861" '
    'stroke-width="4" stroke-dasharray="14 12"/>'
    '<path d="M244 250l70 120h-140z" fill="none" stroke="#6b6861" stroke-width="8" '
    'stroke-linejoin="round"/><circle cx="244" cy="430" r="8" fill="#6b6861"/>'
    "</svg>"
)


def placeholder() -> Response:
    return Response(_PLACEHOLDER, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


def cache_path(settings: Settings, size: str, card_id: str, face: str, url: str) -> Path:
    """Where an image lives on disk. The name carries the version Scryfall puts in the
    URL's query string, so a rescanned image replaces the old one."""
    version = re.sub(r"\D", "", urlsplit(url).query)[:20] or "0"
    return settings.images_dir / size / card_id[:2] / f"{card_id}-{face}-{version}.jpg"


def http_client(app: FastAPI) -> httpx.Client:
    if app.state.http_client is None:
        app.state.http_client = httpx.Client(headers=HEADERS, timeout=20, follow_redirects=True)
    return app.state.http_client


def download(client: httpx.Client, url: str, path: Path) -> bool:
    """Fetch ``url`` into ``path``. False, and nothing left on disk, if anything is off."""
    host = urlsplit(url).hostname or ""
    if host != ALLOWED_HOST and not host.endswith("." + ALLOWED_HOST):
        return False
    try:
        response = client.get(url)
        if (
            response.status_code != 200
            or not response.headers.get("content-type", "").startswith("image/")
            or not 0 < len(response.content) <= MAX_BYTES
        ):
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            partial.write_bytes(response.content)
            partial.replace(path)
        finally:
            partial.unlink(missing_ok=True)
        # older versions of the same image
        stem = path.name.rsplit("-", 1)[0]
        for old in path.parent.glob(f"{stem}-*.jpg"):
            if old != path:
                old.unlink(missing_ok=True)
    except (httpx.HTTPError, OSError):
        return False
    return True


@router.get("/img/{size}/{card_id}/{face}")
def image(
    size: str,
    card_id: str,
    face: str,
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(db.get_conn)],
) -> Response:
    column = COLUMNS.get((size, face))  # a fixed column name, never user text
    if column is None or not _ID.fullmatch(card_id):
        raise HTTPException(404)
    if not db.has_catalog(conn):
        return placeholder()
    row = conn.execute(f"SELECT {column} FROM cat.printings WHERE id = ?", (card_id,)).fetchone()
    if row is None:
        raise HTTPException(404)
    url = row[0]
    if not url:
        return placeholder()
    path = cache_path(request.app.state.settings, size, card_id, face, url)
    if not path.exists() and not download(http_client(request.app), url, path):
        return placeholder()
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
