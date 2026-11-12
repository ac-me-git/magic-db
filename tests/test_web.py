"""Browsing pages (SPEC §7)."""

import re
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from magicdb import i18n, search
from magicdb.app import create_app
from magicdb.catalog.ingest import is_extra
from magicdb.templating import build_templates

UNKNOWN = "00000000-0000-4000-8000-000000000000"


@pytest.fixture
def client(catalog):
    return TestClient(create_app(catalog))


@pytest.fixture
def italian(catalog):
    return TestClient(create_app(replace(catalog, lang="it")))


def card_id(fixture_cards, name, set_code=None):
    return next(
        c["id"]
        for c in fixture_cards
        if c["name"].startswith(name) and set_code in (None, c["set"])
    )


# --- home and filter panel ------------------------------------------------------------


def test_home_shows_catalog_status_and_panel(client, fixture_cards):
    page = client.get("/")
    assert page.status_code == 200
    assert f"{len(fixture_cards)} printings in the catalog" in page.text
    assert 'action="/filter"' in page.text
    for option in ['<option value="Dragon">', '<option value="Flying">', '<option value="arn">']:
        assert option in page.text
    assert "<option>modern</option>" in page.text
    assert "Kept in" not in page.text  # no locations yet


def test_home_lists_locations_once_there_are_some(client, catalog):
    from magicdb import db

    conn = db.connect(catalog)
    with conn:
        conn.execute("INSERT INTO locations (name, created_at) VALUES ('Blue box', 'now')")
    conn.close()
    page = client.get("/")
    assert "Kept in" in page.text and "<option>Blue box</option>" in page.text


def test_filter_panel_redirects_to_the_search_it_built(client):
    response = client.get(
        "/filter?name=bolt&c=r&c=g&color_mode=eq&mv_max=3&text=&order=name",
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = urlsplit(response.headers["location"])
    assert location.path == "/search"
    assert parse_qs(location.query) == {"q": ["bolt c=rg mv<=3"]}

    page = client.get("/filter?subtype=Dragon&c=r")
    assert page.status_code == 200
    assert 'value="t:Dragon c:r"' in page.text  # the search box shows what was built
    assert "Shivan Dragon" in page.text


def test_empty_filter_panel_searches_everything(client):
    assert client.get("/filter", follow_redirects=False).headers["location"] == "/search?q="


# --- search results -------------------------------------------------------------------


def test_search_grid(client, fixture_cards):
    page = client.get("/search?q=t:dragon")
    assert page.status_code == 200
    assert 'value="t:dragon"' in page.text
    assert "Shivan Dragon" in page.text and "Serra Angel" not in page.text
    dragon = card_id(fixture_cards, "Shivan Dragon")
    assert f'href="/card/{dragon}"' in page.text
    assert f'src="/img/normal/{dragon}/front"' in page.text
    assert 'loading="lazy"' in page.text
    assert "<table" not in page.text


def test_search_list(client):
    page = client.get("/search?q=t:dragon&view=list")
    assert page.status_code == 200
    assert "<table" in page.text and "Shivan Dragon" in page.text
    assert 'src="/img/' not in page.text  # mana symbols are images too, cards are not shown
    assert 'name="view" value="list"' in page.text  # a new search keeps the view
    assert client.get("/search?q=t:dragon&view=cinema").text.count("<img") > 0


def test_search_counts(client, fixture_cards):
    shown = [c for c in fixture_cards if not is_extra(c)]
    assert f"{len({c['oracle_id'] for c in shown})} cards" in client.get("/search").text
    assert f"{len(shown)} printings" in client.get("/search?q=unique:prints").text


def test_one_result_is_singular(client, italian):
    assert ">\n    1 card\n" in client.get('/search?q=!"Lightning Bolt"').text
    assert "1 printing\n" in client.get('/search?q=!"Sol Ring" unique:prints').text
    assert "1 carta\n" in italian.get('/search?q=!"Lightning Bolt"').text


def test_numbers_follow_the_language(catalog):
    english = build_templates(catalog).env.filters
    italian = build_templates(replace(catalog, lang="it")).env.filters
    dollars = build_templates(replace(catalog, currency="usd")).env.filters
    assert english["count"](120054) == "120,054"
    assert italian["count"](120054) == "120.054"
    assert english["money"](1234.5) == "€1,234.50"
    assert italian["money"](1234.5) == "€1.234,50"
    assert dollars["money"](0.5) == "$0.50"
    assert english["money"](None) == italian["money"](None) == "—"


def test_bad_query_explains_itself_with_status_200(client):
    page = client.get("/search?q=foo:bar")
    assert page.status_code == 200
    assert "This search cannot be understood" in page.text
    assert "is not something you can search by" in page.text
    assert 'value="foo:bar"' in page.text  # the text stays in the box to be fixed
    assert 'href="/syntax"' in page.text


def test_query_text_is_escaped(client):
    page = client.get('/search?q=<script>alert(1)</script>"')
    assert page.status_code == 200
    assert "<script>alert" not in page.text


def test_did_you_mean(client):
    page = client.get("/search?q=lightnig bolt")
    assert "No cards found" in page.text and "Did you mean" in page.text
    assert ">Lightning Bolt</a>" in page.text
    assert client.get("/search?q=qqqqzzzz").text.count("Did you mean") == 0


def test_paging(client, fixture_cards):
    shown = len([c for c in fixture_cards if not is_extra(c)])
    pages = -(-shown // search.PER_PAGE)
    first = client.get("/search?q=unique:prints")
    assert f"Page 1 of {pages}" in first.text
    assert 'rel="next"' in first.text and 'rel="prev"' not in first.text
    assert "page=2" in first.text
    assert first.text.count('src="/img/normal/') == search.PER_PAGE

    last = client.get(f"/search?q=unique:prints&page={pages}")
    assert f"Page {pages} of {pages}" in last.text
    assert 'rel="prev"' in last.text and 'rel="next"' not in last.text

    for junk in ("abc", "-3", "0", "99999999999999999999", "1.5"):
        assert "Page 1 of" in client.get(f"/search?q=unique:prints&page={junk}").text
    assert "Page" not in client.get("/search?q=t:dragon").text  # a single page has no pager


def test_htmx_request_gets_only_the_results(client):
    full = client.get("/search?q=t:dragon")
    partial = client.get("/search?q=t:dragon", headers={"HX-Request": "true"})
    assert partial.status_code == 200
    assert "<html" not in partial.text and "site-header" not in partial.text
    assert "Shivan Dragon" in partial.text
    assert 'hx-target="#results"' in full.text and 'id="results"' in full.text
    bad = client.get("/search?q=foo:bar", headers={"HX-Request": "true"})
    assert "<html" not in bad.text and "cannot be understood" in bad.text


# --- card page ------------------------------------------------------------------------


def test_card_page(client, fixture_cards):
    bolt = card_id(fixture_cards, "Lightning Bolt", "lea")
    page = client.get(f"/card/{bolt}")
    assert page.status_code == 200
    assert "<title>Lightning Bolt" in page.text
    for text in ["Christopher Rush", "Limited Edition Alpha", "1993-08-05", "All printings (6)"]:
        assert text in page.text
    assert "Lightning Bolt deals 3 damage to any target." in page.text
    assert "vintage" in page.text
    assert f'src="/img/normal/{bolt}/front"' in page.text
    assert "flip-toggle" not in page.text
    for other in ("m10", "2x2"):
        assert f'href="/card/{card_id(fixture_cards, "Lightning Bolt", other)}"' in page.text
    assert 'class="current"' in page.text
    assert "scryfall.com" in page.text


def test_card_page_two_faces(client, fixture_cards):
    delver = card_id(fixture_cards, "Delver of Secrets")
    page = client.get(f"/card/{delver}")
    assert "flip-toggle" in page.text and "Turn over" in page.text
    assert f'src="/img/normal/{delver}/back"' in page.text
    assert "Insectile Aberration" in page.text
    assert "1/1" in page.text and "3/2" in page.text
    assert "None" not in page.text and "/</p>" not in page.text


def test_card_page_special_cards(client, fixture_cards):
    jace = client.get(f"/card/{card_id(fixture_cards, 'Jace, the Mind Sculptor')}").text
    assert "Loyalty: 3" in jace
    battle = client.get(f"/card/{card_id(fixture_cards, 'Invasion of Zendikar')}").text
    assert "Defense: 3" in battle
    goyf = client.get(f"/card/{card_id(fixture_cards, 'Tarmogoyf')}").text
    assert "*/1+*" in goyf
    split = client.get(f"/card/{card_id(fixture_cards, 'Fire // Ice')}").text
    assert "flip-toggle" not in split and 'alt="{R}"' in split and 'alt="{U}"' in split


@pytest.mark.parametrize("card", [UNKNOWN, "nonsense", "1%27%20OR%201=1"])
def test_unknown_card_is_not_found(client, card):
    page = client.get(f"/card/{card}")
    assert page.status_code == 404 and "Not found" in page.text


def test_unknown_page_is_not_found(client):
    page = client.get("/no/such/page")
    assert page.status_code == 404 and "Back to the search" in page.text


# --- syntax page ----------------------------------------------------------------------


def test_syntax_page_lists_every_filter(client):
    page = client.get("/syntax")
    assert page.status_code == 200
    for definition in search.FILTERS:
        assert definition.summary.replace("'", "&#39;") in page.text
        for key in definition.keys:
            assert f"<code>{key}</code>" in page.text
    assert 'href="/search?q=mv%3C%3D3"' in page.text


def test_translations_are_complete():
    english, italian = i18n.strings("en"), i18n.strings("it")
    filter_keys = {f"syntax.filter.{d.name}" for d in search.FILTERS}
    assert set(italian) == set(english) | filter_keys
    for key, text in english.items():
        # the same {placeholders} in both languages
        assert sorted(re.findall(r"\{\w+\}", text)) == sorted(
            re.findall(r"\{\w+\}", italian[key])
        ), key


def test_italian_interface(italian, fixture_cards):
    assert "Cerca con i filtri" in italian.get("/").text
    page = italian.get("/search?q=t:dragon")
    assert '<html lang="it">' in page.text and "carte" in page.text
    assert "Sintassi di ricerca" in italian.get("/syntax").text
    assert "Possiedo proprio questa stampa" in italian.get("/syntax").text
    bolt = card_id(fixture_cards, "Lightning Bolt", "lea")
    assert "Tutte le stampe (6)" in italian.get(f"/card/{bolt}").text
    assert "Forse cercavi" in italian.get("/search?q=lightnig bolt").text


# --- autocomplete ---------------------------------------------------------------------


def test_autocomplete(client):
    names = client.get("/api/autocomplete?q=light").json()
    assert names[0].startswith("Light") and "Lightning Bolt" in names
    assert "Lightning Bolt" in client.get("/api/autocomplete?q=bolt").json()
    assert client.get("/api/autocomplete?q=LIGHTNING BO").json() == ["Lightning Bolt"]
    prefixed = client.get("/api/autocomplete?q=an").json()
    starts = [name.lower().startswith("an") for name in prefixed]
    assert starts == sorted(starts, reverse=True)  # names starting with it come first
    assert len(client.get("/api/autocomplete?q=er").json()) == 15


@pytest.mark.parametrize("q", ["", "l", " ", "%", "__", "zzzzzz"])
def test_autocomplete_with_nothing_to_offer(client, q):
    assert client.get("/api/autocomplete", params={"q": q}).json() == []


# --- no catalog yet -------------------------------------------------------------------


def test_pages_without_a_catalog(settings):
    client = TestClient(create_app(settings))
    home = client.get("/")
    assert home.status_code == 200
    assert "has not been downloaded yet" in home.text and 'action="/filter"' not in home.text
    for path in ("/search?q=bolt", f"/card/{UNKNOWN}"):
        page = client.get(path)
        assert page.status_code == 503 and "The card catalog is not here yet" in page.text
    assert client.get("/syntax").status_code == 200
    assert client.get("/api/autocomplete?q=bolt").json() == []
