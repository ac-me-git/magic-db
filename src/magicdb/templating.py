"""Jinja environment shared by every page, and the per-visitor interface language."""

import hashlib
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Request
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from jinja2.runtime import Context

from magicdb.config import LANGS, Settings
from magicdb.i18n import translator
from magicdb.symbols import render_symbols

LANG_COOKIE = "magicdb_lang"
ONE_YEAR = 365 * 24 * 60 * 60
_SYMBOL = {"eur": "€", "usd": "$"}


SCOPES = ("all", "collection")


def search_url(q: str, page: int = 1, view: str = "grid", scope: str = "all") -> str:
    params: dict[str, str | int] = {"q": q}
    if page > 1:
        params["page"] = page
    if view != "grid":
        params["view"] = view
    if scope != "all":
        params["scope"] = scope
    return "/search?" + urlencode(params)


def scope_of(request: Request) -> str:
    """Where the visitor is searching: every card, or only their collection."""
    scope = request.query_params.get("scope", "all")
    return scope if scope in SCOPES else "all"


def safe_next(target: str | None, default: str = "/") -> str:
    """A redirect target is accepted only if it is a path on this site."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return default


def _accepted(header: str) -> list[str]:
    """Languages of an Accept-Language header, most wanted first: 'it-IT,en;q=0.8' -> it, en."""
    weighted = []
    for position, part in enumerate(header.split(",")):
        tag, _, quality = part.strip().partition(";q=")
        try:
            weight = float(quality) if quality else 1.0
        except ValueError:
            weight = 0.0
        if tag and weight > 0:
            weighted.append((-weight, position, tag.split("-")[0].lower()))
    return [language for _, _, language in sorted(weighted)]


def resolve_language(request: Request, default: str) -> str:
    """The visitor's choice (cookie), else their browser's language, else the default."""
    chosen = request.cookies.get(LANG_COOKIE)
    if chosen in LANGS:
        return chosen
    for language in _accepted(request.headers.get("accept-language", "")):
        if language in LANGS:
            return language
    return default


def _localized(number: str, lang: str) -> str:
    """'1,234.50' as written in the interface language."""
    return number.translate(str.maketrans(",.", ".,")) if lang == "it" else number


def format_count(value: int, lang: str) -> str:
    return _localized(f"{value:,}", lang)


def format_money(value: float | None, currency: str, lang: str) -> str:
    if value is None:
        return "—"
    return f"{_SYMBOL[currency]}{_localized(f'{value:,.2f}', lang)}"


_translator = lru_cache(translator)


STATIC_DIR = Path(__file__).parent / "static"


@lru_cache
def asset(name: str) -> str:
    """The address of a static file, with a fingerprint of its content.

    A browser keeps style sheets and scripts for a long time. The fingerprint changes
    when the file does, so after an upgrade nobody is left with yesterday's style on
    today's pages.
    """
    digest = hashlib.sha256((STATIC_DIR / name).read_bytes()).hexdigest()[:10]
    return f"/static/{name}?v={digest}"


def build_templates(settings: Settings) -> Jinja2Templates:
    def per_request(request: Request) -> dict[str, object]:
        lang = resolve_language(request, settings.lang)
        here = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return {
            "t": _translator(lang),
            "lang": lang,
            "languages": LANGS,
            "here": here,
            "locked": getattr(request.app.state, "locked", False),
            "scope": scope_of(request),
        }

    templates = Jinja2Templates(
        directory=Path(__file__).parent / "templates", context_processors=[per_request]
    )

    @pass_context
    def money(context: Context, value: float | None) -> str:
        return format_money(value, settings.currency, context.get("lang", settings.lang))

    @pass_context
    def count(context: Context, value: int) -> str:
        return format_count(value, context.get("lang", settings.lang))

    templates.env.globals.update(currency=settings.currency, search_url=search_url, asset=asset)
    templates.env.filters.update(money=money, count=count, symbols=render_symbols)
    return templates
