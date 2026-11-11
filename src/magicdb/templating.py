"""Jinja environment shared by every page."""

from pathlib import Path
from urllib.parse import urlencode

from fastapi.templating import Jinja2Templates

from magicdb.config import Settings
from magicdb.i18n import translator

_SYMBOL = {"eur": "€", "usd": "$"}


def search_url(q: str, page: int = 1, view: str = "grid") -> str:
    params: dict[str, str | int] = {"q": q}
    if page > 1:
        params["page"] = page
    if view != "grid":
        params["view"] = view
    return "/search?" + urlencode(params)


def build_templates(settings: Settings) -> Jinja2Templates:
    templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

    def localized(number: str) -> str:
        """'1,234.50' as written in the interface language."""
        if settings.lang == "it":
            return number.translate(str.maketrans(",.", ".,"))
        return number

    def money(value: float | None) -> str:
        if value is None:
            return "—"
        return f"{_SYMBOL[settings.currency]}{localized(f'{value:,.2f}')}"

    def count(value: int) -> str:
        return localized(f"{value:,}")

    templates.env.globals.update(
        t=translator(settings.lang),
        lang=settings.lang,
        currency=settings.currency,
        search_url=search_url,
    )
    templates.env.filters.update(money=money, count=count)
    return templates
