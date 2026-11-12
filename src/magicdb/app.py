"""FastAPI application factory."""

import base64
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint

from magicdb import __version__, admin, db, images, jobs, lock, symbols, web
from magicdb.catalog import publish
from magicdb.collection import routes as collection_routes
from magicdb.collection import transfer
from magicdb.config import Settings, load_settings
from magicdb.templating import build_templates

_PKG = Path(__file__).parent
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def _password_matches(header: str, expected: str) -> bool:
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        _, _, given = base64.b64decode(encoded, validate=True).decode("utf-8").partition(":")
    except (ValueError, UnicodeDecodeError):
        return False
    return secrets.compare_digest(given.encode(), expected.encode())


def create_app(
    settings: Settings | None = None, http_client: httpx.Client | None = None
) -> FastAPI:
    """Build the app. ``http_client`` is for tests; by default one is created on first use."""
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Only a real server gets here, not a test client: the scheduler downloads
        # the catalog and writes backups, which tests must never do by accident.
        jobs.mark_interrupted(settings)
        if settings.scheduler:
            app.state.scheduler = jobs.Scheduler(settings)
            app.state.scheduler.start()
        yield
        if app.state.scheduler is not None:
            app.state.scheduler.stop()
        if app.state.http_client is not None:
            app.state.http_client.close()

    app = FastAPI(
        title="magic-db", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan
    )
    app.state.settings = settings
    app.state.http_client = http_client
    app.state.scheduler = None
    app.state.templates = build_templates(settings)

    db.migrate(settings)
    publish.cleanup_old(settings)
    app.state.locked = lock.load(settings)

    app.mount("/static", StaticFiles(directory=_PKG / "static"), name="static")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # Middlewares run in the reverse of the order they are added here:
    # password first, then same origin, then the safety lock.

    @app.middleware("http")
    async def safety_lock(request: Request, call_next: RequestResponseEndpoint) -> Response:
        """While the collection is locked, refuse whatever could change it."""
        if (
            app.state.locked
            and request.method not in SAFE_METHODS
            and not lock.allowed_while_locked(request.url.path)
        ):
            return web.render(request, "locked.html", 423)
        return await call_next(request)

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

    @app.middleware("http")
    async def password(request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Optional HTTP Basic auth (MAGICDB_PASSWORD): any user name, that password."""
        if settings.password and request.url.path != "/healthz":
            if not _password_matches(request.headers.get("authorization", ""), settings.password):
                return PlainTextResponse(
                    "Password required.",
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="magic-db", charset="UTF-8"'},
                )
        return await call_next(request)

    app.include_router(web.router)
    app.include_router(admin.router)
    app.include_router(lock.router)
    app.include_router(collection_routes.router)
    app.include_router(transfer.router)
    app.include_router(images.router)
    app.include_router(symbols.router)
    app.add_exception_handler(404, web.not_found)
    return app
