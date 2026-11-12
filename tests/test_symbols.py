"""Mana and tap symbols as images (SPEC §7)."""

import httpx
import pytest
from fastapi.testclient import TestClient

from magicdb.app import create_app
from magicdb.symbols import render_symbols, symbol_code

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle r="50"/></svg>'


def img(code: str, symbol: str) -> str:
    return f'<img class="ms" src="/symbol/{code}.svg" alt="{symbol}" loading="lazy">'


@pytest.mark.parametrize(
    ("symbol", "code"),
    [
        ("R", "R"),
        ("T", "T"),
        ("10", "10"),
        ("2/W", "2W"),
        ("W/U/P", "WUP"),
        ("CHAOS", "CHAOS"),
        ("∞", "INFINITY"),
        ("½", "HALF"),
        ("1000000", "1000000"),
    ],
)
def test_symbol_code(symbol, code):
    assert symbol_code(symbol) == code


def test_render_mana_cost():
    assert render_symbols("{2}{R}{R}") == img("2", "{2}") + img("R", "{R}") * 2
    assert render_symbols("{1}{R} // {1}{U}") == (
        img("1", "{1}") + img("R", "{R}") + " // " + img("1", "{1}") + img("U", "{U}")
    )
    assert render_symbols("{R/W}{2/U}{G/U/P}") == (
        img("RW", "{R/W}") + img("2U", "{2/U}") + img("GUP", "{G/U/P}")
    )
    assert render_symbols(None) == "" and render_symbols("") == ""


def test_render_rules_text():
    text = "{T}: Add {G}.\n{Q}, Pay {X}: Draw a card."
    assert render_symbols(text) == (
        f"{img('T', '{T}')}: Add {img('G', '{G}')}.\n"
        f"{img('Q', '{Q}')}, Pay {img('X', '{X}')}: Draw a card."
    )


def test_render_leaves_everything_else_alone():
    for plain in ["Flying", "{}", "{t}", "{not a symbol}", "{TOOLONGTOBE}", "{R", "R}", "+1: x"]:
        assert render_symbols(plain) == plain


def test_render_escapes_the_text_around_symbols():
    rendered = render_symbols('<script>alert("x")</script> {T} & <b>')
    assert "<script>" not in rendered and "<b>" not in rendered
    assert rendered.startswith("&lt;script&gt;") and "&amp;" in rendered
    assert img("T", "{T}") in rendered
    assert render_symbols('{R"><script>}') == "{R&#34;&gt;&lt;script&gt;}"


# --- the pages use it -----------------------------------------------------------------


@pytest.fixture
def scryfall():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("/NOPE.svg"):
            return httpx.Response(404, content=b"not found")
        return httpx.Response(200, content=SVG, headers={"content-type": "image/svg+xml"})

    handler.calls = calls
    return handler


@pytest.fixture
def client(catalog, scryfall):
    http = httpx.Client(transport=httpx.MockTransport(scryfall))
    return TestClient(create_app(catalog, http_client=http))


def card_id(fixture_cards, name):
    return next(c["id"] for c in fixture_cards if c["name"].startswith(name))


def test_card_page_shows_cost_and_text_with_symbols(client, fixture_cards):
    elves = client.get(f"/card/{card_id(fixture_cards, 'Llanowar Elves')}").text
    assert f'<span class="mana">{img("G", "{G}")}</span>' in elves
    assert f"{img('T', '{T}')}: Add {img('G', '{G}')}." in elves

    split = client.get(f"/card/{card_id(fixture_cards, 'Fire // Ice')}").text
    assert img("1", "{1}") + img("R", "{R}") in split
    assert img("1", "{1}") + img("U", "{U}") in split

    reckoner = client.get(f"/card/{card_id(fixture_cards, 'Boros Reckoner')}").text
    assert img("RW", "{R/W}") * 3 in reckoner


def test_list_view_shows_cost_with_symbols(client):
    page = client.get('/search?q=!"Shivan Dragon"&view=list').text
    assert img("4", "{4}") + img("R", "{R}") * 2 in page
    land = client.get('/search?q=!"Dryad Arbor"&view=list').text
    assert '<td class="mana wide"></td>' in land  # no cost, no stray text


# --- the route ------------------------------------------------------------------------


def test_miss_downloads_then_hit_serves_from_disk(client, scryfall, catalog):
    first = client.get("/symbol/2W.svg")
    assert first.status_code == 200
    assert first.headers["content-type"].startswith("image/svg+xml")
    assert first.content == SVG
    assert "immutable" in first.headers["cache-control"]
    assert "default-src 'none'" in first.headers["content-security-policy"]
    assert first.headers["x-content-type-options"] == "nosniff"
    assert scryfall.calls == ["https://svgs.scryfall.io/card-symbols/2W.svg"]
    assert [p.name for p in catalog.symbols_dir.iterdir()] == ["2W.svg"]

    assert client.get("/symbol/2W.svg").content == SVG
    assert len(scryfall.calls) == 1  # served from disk


def test_unknown_symbol_is_not_found_and_not_cached(client, scryfall, catalog):
    assert client.get("/symbol/NOPE.svg").status_code == 404
    assert not catalog.symbols_dir.exists() or list(catalog.symbols_dir.iterdir()) == []


@pytest.mark.parametrize(
    "path",
    ["/symbol/r.svg", "/symbol/R-W.svg", "/symbol/..%2Fx.svg", "/symbol/.svg", "/symbol/R"],
)
def test_bad_symbol_names_never_reach_scryfall(client, scryfall, path):
    assert client.get(path).status_code == 404
    assert scryfall.calls == []


def test_works_without_a_catalog(settings, scryfall):
    http = httpx.Client(transport=httpx.MockTransport(scryfall))
    client = TestClient(create_app(settings, http_client=http))
    assert client.get("/symbol/T.svg").status_code == 200
