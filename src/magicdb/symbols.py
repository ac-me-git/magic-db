"""Mana and tap symbols: ``{2}{R}``, ``{T}`` shown as images (SPEC §7).

The pictures are Scryfall's SVG card symbols, fetched on first use and cached on disk
like card images, so the repository itself carries none of them.
"""

import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from markupsafe import Markup, escape

from magicdb import images

router = APIRouter()

SOURCE = "https://svgs.scryfall.io/card-symbols/{code}.svg"
_SYMBOL = re.compile(r"\{([A-Z0-9/½∞]{1,9})\}")
_CODE = re.compile(r"[A-Z0-9]{1,12}")
_NAMES = {"∞": "INFINITY", "½": "HALF"}


def symbol_code(symbol: str) -> str:
    """'2/W' -> '2W', '∞' -> 'INFINITY': the file name Scryfall uses for a symbol."""
    code = symbol.replace("/", "")
    for char, name in _NAMES.items():
        code = code.replace(char, name)
    return code


def render_symbols(text: str | None) -> Markup:
    """Escape ``text`` and turn every ``{X}`` into a small image.

    The ``alt`` keeps the original ``{X}``, so the text still reads, copies and
    searches correctly, and shows as is when a picture cannot be loaded.
    """

    def image(match: re.Match[str]) -> str:
        code = symbol_code(match.group(1))
        return f'<img class="ms" src="/symbol/{code}.svg" alt="{match.group(0)}" loading="lazy">'

    # escape first: none of the characters a symbol can contain is touched by it
    return Markup(_SYMBOL.sub(image, str(escape(text or ""))))


@router.get("/symbol/{code}.svg")
def symbol(code: str, request: Request) -> FileResponse:
    if not _CODE.fullmatch(code):
        raise HTTPException(404)
    path = request.app.state.settings.symbols_dir / f"{code}.svg"
    if not path.exists():
        client = images.http_client(request.app)
        if not images.download(client, SOURCE.format(code=code), path):
            raise HTTPException(404)
    return FileResponse(
        path,
        media_type="image/svg+xml",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            # an SVG fetched from elsewhere must never run script on this origin
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
            "X-Content-Type-Options": "nosniff",
        },
    )
