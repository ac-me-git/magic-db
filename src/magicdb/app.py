"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint

from magicdb import __version__, db, images, symbols, web
from magicdb.catalog import publish
from magicdb.collection import routes as collection_routes
from magicdb.config import Settings, load_settings
from magicdb.templating import build_templates

_PKG = Path(__file__).parent
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


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

    @app.middleware("http")
    async def same_origin(request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Refuse a change that another site asks the visitor's browser to make.

        Browsers send ``Origin`` with every cross-site POST; it must be this site.
        A reverse proxy may pass the public name in ``X-Forwarded-Host``.
        """
        origin = request.headers.get("origin")
        if request.method not in SAFE_METHODS and origin is not None:
            hosts = {request.headers.get("host"), request.headers.get("x-forwarded-host")}
            if urlsplit(origin).netloc not in hosts - {None}:
                return PlainTextResponse("Cross-site request refused.", status_code=403)
        return await call_next(request)

    app.include_router(web.router)
    app.include_router(collection_routes.router)
    app.include_router(images.router)
    app.include_router(symbols.router)
    app.add_exception_handler(404, web.not_found)
    return app
