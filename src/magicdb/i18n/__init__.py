"""UI strings. Card data is always English; only the interface is translated."""

import json
from collections.abc import Callable
from functools import cache
from pathlib import Path

_DIR = Path(__file__).parent


@cache
def _strings(lang: str) -> dict[str, str]:
    return json.loads((_DIR / f"{lang}.json").read_text(encoding="utf-8"))


def translator(lang: str) -> Callable[..., str]:
    """Return ``t(key, **values)``; falls back to English, then to the key itself."""
    strings = _strings(lang)
    fallback = _strings("en")

    def t(key: str, **values: object) -> str:
        text = strings.get(key) or fallback.get(key) or key
        return text.format(**values) if values else text

    return t
