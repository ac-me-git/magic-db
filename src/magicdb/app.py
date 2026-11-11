"""FastAPI application factory."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from magicdb import __version__, db
from magicdb.catalog import publish
from magicdb.config import Settings, load_settings
from magicdb.i18n import translator

_PKG = Path(__file__).parent


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="magic-db", version=__version__, docs_url=None, redoc_url=None)
    app.state.settings = settings

    db.migrate(settings)
    publish.cleanup_old(settings)

    templates = Jinja2Templates(directory=_PKG / "templates")
    templates.env.globals.update(t=translator(settings.lang), lang=settings.lang)
    app.state.templates = templates

    app.mount("/static", StaticFiles(directory=_PKG / "static"), name="static")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "home.html")

    return app
