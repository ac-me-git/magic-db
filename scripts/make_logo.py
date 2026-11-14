"""Draw the AMTG logo: the binder mark, the icons and the wordmarks.

    uv run python scripts/make_logo.py

The mark is a page of a card binder: four cards, one of them amber. The lettering is
drawn here too, as strokes on a grid, so the files need no font. Only the standard
library is used: the PNG and ICO files are rasterised by the small renderer below.

Writes the kit in branding/ and the copies the program and the website use (favicons,
the header's logo); the executable takes branding/amtg.ico. A test fails when a file
is not what this script draws.
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from math import ceil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

VIOLET = "#4b3f8f"
VIOLET_LIGHT = "#9d90f0"
AMBER = "#e0a030"
INK = "#15141b"
INK_TEXT = "#1d1b26"
PAPER = "#ffffff"
PAPER_TEXT = "#ecebf2"


@dataclass(frozen=True)
class Theme:
    card: str  # the three plain cards
    accent: str  # the amber card
    text: str  # lettering
    highlight: str  # ".FANS" and the initials of the full name
    tile: str | None = None  # the icon's background, when it has one


ON_LIGHT = Theme(card=VIOLET, accent=AMBER, text=INK_TEXT, highlight=VIOLET)
ON_DARK = Theme(card=VIOLET_LIGHT, accent=AMBER, text=PAPER_TEXT, highlight=VIOLET_LIGHT)
ICON = Theme(card=PAPER, accent=AMBER, text=PAPER, highlight=PAPER, tile=VIOLET)
ICON_DARK = Theme(card=VIOLET_LIGHT, accent=AMBER, text=PAPER, highlight=PAPER, tile=INK)
# The header's logo is coloured by app.css (".x" stands for class="x"), so that it
# follows the page between light and dark.
STYLED = Theme(card=".logo-card", accent=".logo-amber", text=".logo-letters", highlight="")

# --- the mark ------------------------------------------------------------------------
# On the icon's 16-unit grid: cards of 4 x 5 with a gap of 2, the top right one amber.
# Every edge falls on a whole unit, so the 16 and 32 pixel icons are sharp.

CARD_W, CARD_H, GAP, CARD_R = 4, 5, 2, 0.75
MARK_W, MARK_H = 2 * CARD_W + GAP, 2 * CARD_H + GAP
CARDS = ((0, 0, False), (1, 0, True), (0, 1, False), (1, 1, False))  # column, row, amber
TILE, TILE_R = 16, 3.5
TILE_X, TILE_Y = (TILE - MARK_W) // 2, (TILE - MARK_H) // 2


def num(value: float) -> str:
    """A coordinate for SVG: at most two decimals, no trailing zeros."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def paint(prop: str, colour: str) -> str:
    """fill="#…" or stroke="#…", or a class when the style sheet gives the colour."""
    return f'class="{colour[1:]}"' if colour.startswith(".") else f'{prop}="{colour}"'


def mark_rects(theme: Theme, x: float, y: float, scale: float) -> list[str]:
    rects = []
    for col, row, amber in CARDS:
        rects.append(
            f'<rect x="{num(x + col * (CARD_W + GAP) * scale)}" '
            f'y="{num(y + row * (CARD_H + GAP) * scale)}" '
            f'width="{num(CARD_W * scale)}" height="{num(CARD_H * scale)}" '
            f'rx="{num(CARD_R * scale)}" {paint("fill", theme.accent if amber else theme.card)}/>'
        )
    return rects


def svg(width: float, height: float, body: list[str], title: str | None, extra: str = "") -> str:
    """A whole SVG file; without a title it is decoration, hidden from screen readers."""
    head = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {num(width)} {num(height)}"'
    if extra:
        head += " " + extra
    if title is None:
        return f'{head} aria-hidden="true">{"".join(body)}</svg>\n'
    return f'{head} role="img" aria-label="{title}"><title>{title}</title>{"".join(body)}</svg>\n'


def icon_svg(theme: Theme) -> str:
    body = [f'<rect width="{TILE}" height="{TILE}" rx="{num(TILE_R)}" fill="{theme.tile}"/>']
    return svg(TILE, TILE, body + mark_rects(theme, TILE_X, TILE_Y, 1), "AMTG")


def mark_svg(theme: Theme) -> str:
    return svg(MARK_W, MARK_H, mark_rects(theme, 0, 0, 1), "AMTG")


# --- the lettering ---------------------------------------------------------------------
# Capitals drawn as centre lines on a grid 40 units tall (0 = cap line, 40 = baseline),
# stroked with round ends. Round letters overshoot by one unit, as in type.

GLYPHS: dict[str, tuple[float, str]] = {
    "A": (32, "M0 40L16 0L32 40M5.6 26H26.4"),
    "C": (35.85, "M35.85 5.15A21 21 0 1 0 35.85 34.85"),
    "E": (24, "M24 0H0V40H24M0 20H20"),
    "F": (24, "M24 0H0V40M0 20H20"),
    "G": (42, "M35.85 5.15A21 21 0 1 0 42 20H24"),
    "H": (30, "M0 0V40M30 0V40M0 20H30"),
    "I": (0, "M0 0V40"),
    "K": (26, "M0 0V40M26 0L0 24M8.5 16.15L26 40"),
    "M": (36, "M0 40V0L18 30L36 0V40"),
    "N": (30, "M0 40V0L30 40V0"),
    "O": (42, "M0 20A21 21 0 1 1 42 20A21 21 0 1 1 0 20Z"),
    "R": (26, "M0 40V0H13A10 10 0 0 1 13 20H0M13 20L26 40"),
    "S": (21, "M18.73 4.75A9.5 9.5 0 1 0 10.5 19A10.5 10.5 0 1 1 1.41 34.75"),
    "T": (30, "M0 0H30M15 0V40"),
    "Z": (26, "M0 0H26L0 40H26"),
    ".": (0, "M0 40L0 40.01"),
}

# Pairs whose shapes leave too much room between them (units of the 40 grid).
KERN: dict[str, float] = {
    "AM": -3,
    "MT": -3,
    "TG": -4,
    "G.": -2,
    "FA": -6,
    "AN": -3,
    "TH": -2,
    "MA": -3,
    "AG": -2,
    "TR": -3,
    "RA": -6,
    "AC": -2,
    "CK": -3,
    "OT": -2,
}


@dataclass(frozen=True)
class Lettering:
    stroke: float  # stroke width, in grid units
    gap: float  # room between two letters, edge to edge
    word_gap: float  # room between two words


HEAVY = Lettering(stroke=8, gap=9, word_gap=26)
TAGLINE = Lettering(stroke=8, gap=11, word_gap=30)


def layout(text: str, style: Lettering) -> tuple[list[tuple[str, float]], float]:
    """Where each letter's centre line starts, and the width of the whole line."""
    placed: list[tuple[str, float]] = []
    edge = 0.0  # right edge of the ink so far
    room = 0.0
    previous = ""
    for char in text:
        if char == " ":
            room = style.word_gap
            previous = ""
            continue
        width, _ = GLYPHS[char]
        left = edge + room + KERN.get(previous + char, 0)
        start = left + style.stroke / 2
        placed.append((char, start))
        edge = start + width + style.stroke / 2
        room = style.gap
        previous = char
    return placed, edge


def text_groups(
    text: str, colours: list[str], style: Lettering, x: float, y: float, scale: float
) -> list[str]:
    """SVG groups for one line; (x, y) is the top left corner of its ink, overshoot included."""
    placed, _ = layout(text, style)
    top = y + (1 + style.stroke / 2) * scale
    by_colour: dict[str, list[str]] = {}
    for (char, start), colour in zip(placed, colours, strict=True):
        _, path = GLYPHS[char]
        by_colour.setdefault(colour, []).append(
            f'<path transform="translate({num(start)} 0)" d="{path}"/>'
        )
    return [
        f'<g transform="translate({num(x)} {num(top)}) scale({num(scale)})" fill="none" '
        f'{paint("stroke", colour)} stroke-width="{num(style.stroke)}" stroke-linecap="round" '
        f'stroke-linejoin="round">{"".join(paths)}</g>'
        for colour, paths in by_colour.items()
    ]


def line_height(style: Lettering) -> float:
    return 42 + style.stroke  # the ink from overshoot to overshoot


@dataclass(frozen=True)
class Line:
    text: str
    highlight: frozenset[int] = frozenset()  # indexes of letters in the highlight colour


def lockup(
    theme: Theme,
    lines: list[Line],
    style: Lettering,
    text_scale: float,
    mark_scale: float,
    line_gap: float = 0,
    title: str | None = "AMTG",
    extra: Callable[[float, float], str] | None = None,
) -> str:
    """The mark on the left, one or more lines of lettering on its right."""
    mark_w, mark_h = MARK_W * mark_scale, MARK_H * mark_scale
    text_x = mark_w + 0.36 * mark_h
    line_h = line_height(style) * text_scale
    block_h = len(lines) * line_h + (len(lines) - 1) * line_gap
    height = max(mark_h, block_h)
    body = mark_rects(theme, 0, (height - mark_h) / 2, mark_scale)
    width = text_x
    y = (height - block_h) / 2
    for line in lines:
        letters = [c for c in line.text if c != " "]
        colours = [
            theme.highlight if i in line.highlight else theme.text for i in range(len(letters))
        ]
        body += text_groups(line.text, colours, style, text_x, y, text_scale)
        width = max(width, text_x + layout(line.text, style)[1] * text_scale)
        y += line_h + line_gap
    return svg(width, height, body, title, extra(width, height) if extra else "")


def initials(text: str) -> frozenset[int]:
    """Indexes (among the letters, spaces skipped) of each word's first letter."""
    found, index = set(), 0
    for word in text.split():
        found.add(index)
        index += len(word)
    return frozenset(found)


FULL_NAME = "Another Magic Tracking Gizmo"
HEAVY_MARK = 1.25 * line_height(HEAVY) / MARK_H  # the mark is 1.25 times as tall as AMTG


def logos(theme: Theme) -> dict[str, str]:
    first, second = "ANOTHER MAGIC", "TRACKING GIZMO"
    whole = f"{first} {second}"
    fans = frozenset(range(4, 9))
    return {
        "amtg": lockup(theme, [Line("AMTG")], HEAVY, 1, HEAVY_MARK),
        "amtg-fans": lockup(
            theme, [Line("AMTG.FANS", fans)], HEAVY, 1, HEAVY_MARK, title="AMTG.FANS"
        ),
        "full-name": lockup(
            theme,
            [Line(first, initials(first)), Line(second, initials(second))],
            TAGLINE,
            0.5,
            HEAVY_MARK,
            line_gap=0.12 * MARK_H * HEAVY_MARK,
            title=FULL_NAME,
        ),
        "full-name-line": lockup(
            theme, [Line(whole, initials(whole))], TAGLINE, 0.62, HEAVY_MARK * 0.8, title=FULL_NAME
        ),
    }


def header_logo() -> str:
    """The header's logo, inline in the page: the link around it carries the name.

    `slice` keeps the left of the picture when the style sheet narrows it to the binder
    alone (on a phone)."""

    def size(width: float, height: float) -> str:
        return (
            f'class="logo" width="{num(width)}" height="{num(height)}" '
            'preserveAspectRatio="xMinYMid slice" focusable="false"'
        )

    return lockup(STYLED, [Line("AMTG")], HEAVY, 1, HEAVY_MARK, title=None, extra=size)


# --- raster icons ----------------------------------------------------------------------


def rgb(colour: str) -> tuple[float, float, float]:
    red, green, blue = (int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return red, green, blue


def coverage(px: int, py: int, box: tuple[float, float, float, float], r: float) -> float:
    """How much of pixel (px, py) a rounded rectangle covers, from 0 to 1."""
    x0, y0, x1, y1 = box
    ox = max(0.0, min(px + 1, x1) - max(px, x0))
    oy = max(0.0, min(py + 1, y1) - max(py, y0))
    if not ox or not oy:
        return 0.0
    in_corner = (px < x0 + r or px + 1 > x1 - r) and (py < y0 + r or py + 1 > y1 - r)
    if not in_corner:
        return ox * oy
    n, inside = 8, 0
    for i in range(n):
        sx = px + (i + 0.5) / n
        if not x0 <= sx <= x1:
            continue
        cx = min(max(sx, x0 + r), x1 - r)
        for j in range(n):
            sy = py + (j + 0.5) / n
            if not y0 <= sy <= y1:
                continue
            cy = min(max(sy, y0 + r), y1 - r)
            if (sx - cx) ** 2 + (sy - cy) ** 2 <= r * r:
                inside += 1
    return inside / (n * n)


def snapped_cards(size: int) -> list[tuple[tuple[float, float, float, float], bool]]:
    """The cards at a pixel size, on whole pixels and centred."""
    unit = size / TILE
    w, h, gap = int(CARD_W * unit + 0.5), int(CARD_H * unit + 0.5), int(GAP * unit + 0.5)
    if (size - 2 * w - gap) % 2:
        gap += 1
    left, top = (size - 2 * w - gap) // 2, (size - 2 * h - gap) // 2
    assert (size - 2 * h - gap) % 2 == 0, size
    return [
        (
            (
                left + col * (w + gap),
                top + row * (h + gap),
                left + col * (w + gap) + w,
                top + row * (h + gap) + h,
            ),
            amber,
        )
        for col, row, amber in CARDS
    ]


def render_icon(theme: Theme, size: int) -> bytes:
    """The icon as straight RGBA bytes, top row first."""
    unit = size / TILE
    pixels = [[0.0, 0.0, 0.0, 0.0] for _ in range(size * size)]  # premultiplied
    assert theme.tile
    shapes = [((0.0, 0.0, float(size), float(size)), TILE_R * unit, theme.tile)]
    for box, amber in snapped_cards(size):
        shapes.append((box, CARD_R * unit, theme.accent if amber else theme.card))
    for box, radius, colour in shapes:
        red, green, blue = rgb(colour)
        x0, y0, x1, y1 = box
        for py in range(int(y0), ceil(y1)):
            for px in range(int(x0), ceil(x1)):
                a = coverage(px, py, box, radius)
                if a:
                    p = pixels[py * size + px]
                    p[0] = red * a + p[0] * (1 - a)
                    p[1] = green * a + p[1] * (1 - a)
                    p[2] = blue * a + p[2] * (1 - a)
                    p[3] = a + p[3] * (1 - a)
    out = bytearray()
    for r, g, b, a in pixels:
        if a:
            out += bytes((round(r / a * 255), round(g / a * 255), round(b / a * 255)))
        else:
            out += b"\0\0\0"
        out.append(round(a * 255))
    return bytes(out)


def png(size: int, rgba: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    stride = size * 4
    raw = b"".join(b"\0" + rgba[y * stride : (y + 1) * stride] for y in range(size))
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def bmp_entry(size: int, rgba: bytes) -> bytes:
    """An icon image the old way: a bottom-up BGRA bitmap and its 1-bit mask."""
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    stride = size * 4
    rows = bytearray()
    for y in reversed(range(size)):
        row = rgba[y * stride : (y + 1) * stride]
        for x in range(size):
            r, g, b, a = row[x * 4 : x * 4 + 4]
            rows += bytes((b, g, r, a))
    mask_stride = (size + 31) // 32 * 4
    mask = bytearray()
    for y in reversed(range(size)):
        bits = bytearray(mask_stride)
        for x in range(size):
            if rgba[(y * size + x) * 4 + 3] == 0:
                bits[x // 8] |= 0x80 >> (x % 8)
        mask += bits
    return header + bytes(rows) + bytes(mask)


ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
PNG_SIZES = (*ICO_SIZES, 512)


def ico(images: dict[int, bytes]) -> bytes:
    """A Windows icon: bitmaps up to 128 pixels, PNG for 256 (as Windows expects)."""
    entries = [(s, png(s, images[s]) if s >= 256 else bmp_entry(s, images[s])) for s in images]
    out = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    for size, data in entries:
        side = 0 if size >= 256 else size
        out += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return out + b"".join(data for _, data in entries)


# --- output ----------------------------------------------------------------------------


def text_files() -> dict[str, str]:
    """Every SVG this script writes, by path from the repository's root."""
    files = {
        "branding/icon.svg": icon_svg(ICON),
        "branding/icon-dark.svg": icon_svg(ICON_DARK),
        "branding/mark-light.svg": mark_svg(ON_LIGHT),
        "branding/mark-dark.svg": mark_svg(ON_DARK),
    }
    for theme, suffix in ((ON_LIGHT, "light"), (ON_DARK, "dark")):
        for name, text in logos(theme).items():
            files[f"branding/{name}-{suffix}.svg"] = text
    files |= {
        "src/magicdb/static/favicon-light.svg": files["branding/icon.svg"],
        "src/magicdb/static/favicon-dark.svg": files["branding/icon-dark.svg"],
        "src/magicdb/templates/logo.svg": header_logo(),
        "site/favicon.svg": files["branding/icon.svg"],
        "site/logo.svg": files["branding/amtg-fans-dark.svg"],  # the website is dark
    }
    return files


def binary_files() -> dict[str, bytes]:
    images = {size: render_icon(ICON, size) for size in PNG_SIZES}
    files = {f"branding/png/icon-{size}.png": png(size, images[size]) for size in PNG_SIZES}
    files["branding/amtg.ico"] = ico({size: images[size] for size in ICO_SIZES})
    return files


def main() -> None:
    texts, binaries = text_files(), binary_files()
    for name, text in texts.items():
        (ROOT / name).parent.mkdir(parents=True, exist_ok=True)
        (ROOT / name).write_text(text, encoding="utf-8", newline="\n")
    for name, data in binaries.items():
        (ROOT / name).parent.mkdir(parents=True, exist_ok=True)
        (ROOT / name).write_bytes(data)
    print(f"wrote {len(texts)} SVG files and {len(binaries)} PNG and ICO files")


if __name__ == "__main__":
    main()
