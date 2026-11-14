"""The logo: one script draws every file, and the program and the website show them."""

import importlib.util
import re
import struct
import sys
from pathlib import Path

from magicdb import web

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(web.__file__).parent / "static"


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses look their module up while it loads
    spec.loader.exec_module(module)
    return module


logo = load_script("make_logo")


def test_every_logo_file_is_the_one_the_script_draws():
    files = logo.text_files()
    assert {
        "src/magicdb/static/favicon-light.svg",
        "src/magicdb/static/favicon-dark.svg",
        "src/magicdb/templates/logo.svg",
        "site/favicon.svg",
        "site/logo.svg",
        "branding/amtg-light.svg",
        "branding/amtg-fans-dark.svg",
        "branding/full-name-light.svg",
    } <= set(files)
    for name, text in files.items():
        on_disk = (ROOT / name).read_text(encoding="utf-8")
        assert on_disk == text, f"{name} is out of date: run python scripts/make_logo.py"


def test_the_drawings_need_no_font_and_no_other_file():
    for name, text in logo.text_files().items():
        assert "<text" not in text and "font" not in text, name
        assert "href" not in text and "<script" not in text and "<image" not in text, name


def test_the_windows_icon_has_every_size_and_its_small_pictures_are_the_drawn_ones():
    data = (ROOT / "branding" / "amtg.ico").read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data)
    assert (reserved, kind) == (0, 1)
    entries = [struct.unpack_from("<BBBBHHII", data, 6 + 16 * i) for i in range(count)]
    assert [width or 256 for width, *_ in entries] == list(logo.ICO_SIZES)
    for width, height, _, _, planes, bits, size, offset in entries:
        image = data[offset : offset + size]
        if width == 0:  # 256 pixels: a PNG, as Windows expects
            assert image.startswith(b"\x89PNG\r\n\x1a\n")
        else:
            assert (height, planes, bits) == (width, 1, 32)
            assert image == logo.bmp_entry(width, logo.render_icon(logo.ICON, width))


def test_the_small_icons_fall_on_whole_pixels():
    for size in (16, 24, 32, 48):
        cards = logo.snapped_cards(size)
        lefts = sorted({box[0] for box, _ in cards})
        rights = sorted({size - box[2] for box, _ in cards})
        assert lefts[0] == rights[0], size  # centred
        assert all(float(v).is_integer() for box, _ in cards for v in box), size
        assert [amber for _, amber in cards].count(True) == 1


def test_the_executable_carries_the_icon():
    build = load_script("build_exe")
    icon = build.ARGUMENTS[build.ARGUMENTS.index("--icon") + 1]
    assert Path(icon) == ROOT / "branding" / "amtg.ico" and Path(icon).is_file()


def test_the_header_shows_the_logo_in_the_colours_of_the_page(client):
    page = client.get("/").text
    top = page.split('<header class="site-header">')[1].split("</header>")[0]
    link = top.split('<a class="brand"')[1].split("</a>")[0]
    assert 'title="Another Magic Tracking Gizmo" aria-label="AMTG"' in link
    assert '<svg xmlns="http://www.w3.org/2000/svg"' in link and 'aria-hidden="true"' in link
    assert link.count('class="logo-card"') == 3 and link.count('class="logo-amber"') == 1
    assert 'class="logo-letters"' in link
    assert 'fill="#' not in link and 'stroke="#' not in link  # the style sheet colours it
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert ".logo-card {\n  fill: var(--accent);" in css
    assert ".logo-letters {\n  stroke: var(--text);" in css


def test_on_a_phone_the_header_keeps_only_the_binder():
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    narrow = css.split("@media (max-width: 30rem) {\n  .logo {")[1].split("}")[0]
    assert f"width: calc(1.75rem * {logo.MARK_W} / {logo.MARK_H});" in narrow
    assert 'preserveAspectRatio="xMinYMid slice"' in logo.header_logo()


def test_every_page_of_the_website_shows_the_logo_beside_its_favicon():
    pages = sorted((ROOT / "site").rglob("*.html"))
    assert len(pages) >= 15
    for page in pages:
        text = page.read_text(encoding="utf-8")
        icon = re.search(r'<link rel="icon" href="([^"]*)favicon\.svg"', text).group(1)
        brand = text.split('<a class="brand"')[1].split("</a>")[0]
        assert f'<img class="logo" src="{icon}logo.svg" alt="AMTG.FANS"' in brand, page
        assert (page.parent / f"{icon}logo.svg").resolve() == (ROOT / "site" / "logo.svg")
