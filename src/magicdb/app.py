"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from magicdb import __version__, db, images, symbols, web
from magicdb.catalog import publish
from magicdb.config import Settings, load_settings
from magicdb.templating import build_templates

_PKG = Path(__file__).parent


def create_app(
    settings: Settings | None = None, http_client: httpx.Client | None = None
) -> FastAPI:
    """Build the app. ``http_client`` is for tests; by default one is created on first use."""
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        if app.state.http_client is not None:
            app.state.http_client.close()

    app = FastAPI(
        title="magic-db", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan
    )
    app.state.settings = settings
    app.state.http_client = http_client
    app.state.templates = build_templates(settings)

    db.migrate(settings)
    publish.cleanup_old(settings)

    app.mount("/static", StaticFiles(directory=_PKG / "static"), name="static")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(web.router)
    app.include_router(images.router)
    app.include_router(symbols.router)
    app.add_exception_handler(404, web.not_found)
    return app
