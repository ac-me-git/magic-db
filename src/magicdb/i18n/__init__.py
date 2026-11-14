"""UI strings. Card data is always English; only the interface is translated."""

import json
from collections.abc import Callable
from functools import cache
from pathlib import Path

from markupsafe import Markup, escape

_DIR = Path(__file__).parent


@cache
def strings(lang: str) -> dict[str, str]:
    return json.loads((_DIR / f"{lang}.json").read_text(encoding="utf-8"))


def translator(lang: str) -> Callable[..., str]:
    """Return ``t(key, default=None, **values)``.

    Falls back to English, then to ``default``, then to the key itself. When a value is
    ``Markup`` (a link, from a template macro) the sentence is escaped and the result is
    ``Markup`` too, so that the link is kept and nothing else in the sentence is HTML.
    """
    chosen = strings(lang)
    english = strings("en")

    def t(key: str, default: str | None = None, **values: object) -> str:
        text = chosen.get(key) or english.get(key) or default or key
        if not values:
            return text
        if any(isinstance(value, Markup) for value in values.values()):
            return escape(text).format(**values)
        return text.format(**values)

    return t
