"""UI strings. Card data is always English; only the interface is translated."""

import json
from collections.abc import Callable
from functools import cache
from pathlib import Path

_DIR = Path(__file__).parent


@cache
def strings(lang: str) -> dict[str, str]:
    return json.loads((_DIR / f"{lang}.json").read_text(encoding="utf-8"))


def translator(lang: str) -> Callable[..., str]:
    """Return ``t(key, default=None, **values)``.

    Falls back to English, then to ``default``, then to the key itself.
    """
    chosen = strings(lang)
    english = strings("en")

    def t(key: str, default: str | None = None, **values: object) -> str:
        text = chosen.get(key) or english.get(key) or default or key
        return text.format(**values) if values else text

    return t
