"""Browsing pages (SPEC §7)."""

import re
from dataclasses import replace
from datetime import date
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from magicdb import db, i18n, search, web
from magicdb.app import create_app
from magicdb.catalog.ingest import is_extra
from magicdb.templating import search_url

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


def show_filters(client) -> None:
    """Choose, in Administration, that the home page shows the filter panel."""
    response = client.post("/admin/home", data={"view": "filters"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/admin#home"


def test_home_opens_on_the_map_of_the_syntax(client, fixture_cards):
    page = client.get("/")
    assert page.status_code == 200
    assert "<h2>Syntax map</h2>" in page.text
    # how big the catalog is, is said in Administration and not here
    assert "printings in the catalog" not in page.text and 'class="hero"' not in page.text
    assert f"<dt>Printings</dt><dd>{len(fixture_cards)}</dd>" in client.get("/admin").text
    # no title, no tagline, and the filters neither shown nor folded away
    assert "<h1" not in page.text and "Every Magic card" not in page.text
    assert 'action="/filter"' not in page.text and "Search by filters" not in page.text
    assert "<details" not in page.text.split("<main>")[1]  # the header's menu is one


def test_home_shows_the_filter_panel_when_administration_says_so(client, fixture_cards):
    show_filters(client)
    page = client.get("/")
    assert page.status_code == 200
    assert "printings in the catalog" not in page.text and 'class="hero"' not in page.text
    assert '<form class="panel" action="/filter" method="get">' in page.text
    assert "<h2>Search by filters</h2>" in page.text
    for option in ['<option value="Dragon">', '<option value="Flying">', '<option value="arn">']:
        assert option in page.text
    assert "<option>modern</option>" in page.text
    assert "Kept in" not in page.text and 'name="location"' not in page.text  # no locations
    # one or the other: the panel takes the place of the map and of the list of sets
    assert "Syntax map" not in page.text and "Recent expansions" not in page.text
    assert "<details" not in page.text.split("<main>")[1] and "<h1" not in page.text


def test_administration_chooses_what_the_home_page_shows(client, catalog):
    page = client.get("/admin").text
    assert '<section class="chart" id="home">' in page and "<h2>Home page</h2>" in page
    chosen = 'class="primary" aria-pressed="true"'
    assert f'<button type="submit" name="view" value="map" {chosen}>Syntax map</button>' in page
    assert '<button type="submit" name="view" value="filters" aria-pressed="false">Filters<' in page

    show_filters(client)
    page = client.get("/admin").text
    assert f'<button type="submit" name="view" value="filters" {chosen}>Filters</button>' in page
    assert '<button type="submit" name="view" value="map" aria-pressed="false">Syntax map<' in page
    # a setting of the server: it is there for another visitor, and after a restart
    assert web.load_home(catalog) == "filters"
    assert 'action="/filter"' in TestClient(create_app(catalog)).get("/").text

    client.post("/admin/home", data={"view": "map"})
    assert web.load_home(catalog) == "map"
    assert "Syntax map" in client.get("/").text and 'action="/filter"' not in client.get("/").text


@pytest.mark.parametrize("view", ["", "both", "MAP", "filters "])
def test_an_unknown_choice_for_the_home_page_changes_nothing(client, catalog, view):
    show_filters(client)
    response = client.post("/admin/home", data={"view": view}, follow_redirects=False)
    assert response.status_code == 303
    assert web.load_home(catalog) == "filters" and 'action="/filter"' in client.get("/").text


def test_a_saved_choice_that_is_not_one_shows_the_map(catalog):
    conn = db.connect(catalog)
    web.save_home(conn, "carousel")
    conn.close()
    assert web.load_home(catalog) == "map"
    assert "Syntax map" in TestClient(create_app(catalog)).get("/").text


def test_the_home_page_can_be_chosen_while_the_collection_is_locked(client):
    client.post("/lock", data={"state": "on"})
    show_filters(client)  # it changes a page, not the collection
    assert 'action="/filter"' in client.get("/").text


def link(query: str) -> str:
    return f'<a class="example" href="{search_url(query)}">'


def test_the_map_of_the_syntax_has_every_filter_once():
    mapped = [name for _, names in web.SYNTAX_MAP for name in names]
    assert sorted(mapped) == sorted(definition.name for definition in search.FILTERS)
    assert [definition.name for _, group in web.syntax_map() for definition in group] == mapped
    english, italian = i18n.strings("en"), i18n.strings("it")
    for strings in (english, italian):
        for group, _ in web.SYNTAX_MAP:
            assert strings[f"map.group.{group}"]
        for name in mapped:
            assert strings[f"map.filter.{name}"]
        for number in range(1, len(web.BASICS) + 1):
            # a short label for the map, the sentence of the syntax page beside it
            assert strings[f"map.basic.{number}"] and strings[f"syntax.basic.{number}"]
        assert f"map.basic.{len(web.BASICS) + 1}" not in strings


def test_home_maps_the_syntax(client):
    page = client.get("/").text
    assert "<h2>Syntax map</h2>" in page
    for title in ("The basics", "The card", "Colours and mana", "Set and printing"):
        assert f"<h3>{title}</h3>" in page
    for title in ("Formats and prices", "My collection", "How results are shown"):
        assert f"<h3>{title}</h3>" in page
    english = i18n.strings("en")
    for definition in search.FILTERS:
        assert f"<dt>{english[f'map.filter.{definition.name}']}</dt>" in page
        for example in definition.example.split("  "):
            assert link(example) in page  # every example is a search to try
    for number, example in enumerate(web.BASICS, 1):
        assert f"<dt>{english[f'map.basic.{number}']}</dt>" in page and link(example) in page
    # what the syntax page says at length is one hover away, and the page itself one link
    assert 'title="I own this exact printing (yes / no)"><dt>I own this printing</dt>' in page
    assert 'title="Quotes keep words together."><dt>Words kept together</dt>' in page
    assert "mv&lt;=3 or pow&gt;=5" in page and '<a href="/syntax">The whole syntax' in page
    # a map: one line for each filter, none of the other ways to write it
    assert "<code>cmc</code>" not in page and "<code>edition</code>" not in page


def test_recent_expansions_are_five_years_of_paper_expansions(catalog):
    conn = db.connect(catalog)

    def codes(on: date) -> list[tuple[str, list[str]]]:
        return [(year, [row["code"] for row in sets]) for year, sets in on_day(on)]

    def on_day(on: date) -> list:
        return web.recent_expansions(conn, on)

    try:
        # newest first, year by year; no Modern Horizons (draft innovation), Commander,
        # Double Masters, core set or digital set, however recent
        assert codes(date(2025, 6, 1)) == [
            ("2023", ["mom"]),
            ("2022", ["dmu"]),
            ("2021", ["khm"]),
            ("2020", ["znr"]),
        ]
        assert on_day(date(2025, 6, 1))[1][1][0]["name"] == "Dominaria United"
        # five years to the day
        assert codes(date(2025, 9, 25))[-1] == ("2020", ["znr"])
        assert codes(date(2025, 9, 26))[-1] == ("2021", ["khm"])
        assert codes(date(2028, 4, 21)) == [("2023", ["mom"])]
        assert codes(date(2028, 4, 22)) == []
        # a set that is announced and already has cards is the most recent of all
        assert codes(date(2022, 1, 1))[0] == ("2023", ["mom"])
        assert codes(date(2024, 2, 29))[-1] == ("2019", ["war", "eld"][::-1])
    finally:
        conn.close()


def test_home_lists_the_recent_expansions(client, monkeypatch):
    monkeypatch.setattr(web, "today", lambda: date(2025, 6, 1))
    page = client.get("/").text
    assert "<h2>Recent expansions</h2>" in page
    assert "The codes of the expansions of the last 5 years, to write as set:code." in page
    chips = [
        '<a class="set-chip" href="/search?q=set%3Amom"><code>mom</code> March of the Machine</a>',
        '<a class="set-chip" href="/search?q=set%3Admu"><code>dmu</code> Dominaria United</a>',
        '<a class="set-chip" href="/search?q=set%3Akhm"><code>khm</code> Kaldheim</a>',
        '<a class="set-chip" href="/search?q=set%3Aznr"><code>znr</code> Zendikar Rising</a>',
    ]
    assert [page.index(chip) for chip in chips] == sorted(page.index(chip) for chip in chips)
    assert page.count('class="set-chip"') == len(chips)
    for year in ("2023", "2022", "2021", "2020"):
        assert f"<dt>{year}</dt>" in page
    assert "<dt>2019</dt>" not in page
    assert page.index("Syntax map") < page.index("Recent expansions")
    assert "100 cards" in client.get("/search?q=set%3Admu").text  # what a chip searches

    monkeypatch.setattr(web, "today", lambda: date(2040, 1, 1))
    page = client.get("/").text
    assert "Recent expansions" not in page and "Syntax map" in page


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
    for example in web.BASICS:  # the same first examples as the home page's map
        assert link(example) in page.text
    assert "Quotes keep words together." in page.text


def test_translations_are_complete():
    english, italian = i18n.strings("en"), i18n.strings("it")
    filter_keys = {f"syntax.filter.{d.name}" for d in search.FILTERS}
    assert set(italian) == set(english) | filter_keys
    for key, text in english.items():
        # the same {placeholders} in both languages
        assert sorted(re.findall(r"\{\w+\}", text)) == sorted(
            re.findall(r"\{\w+\}", italian[key])
        ), key


def test_italian_interface(italian, fixture_cards, monkeypatch):
    monkeypatch.setattr(web, "today", lambda: date(2025, 6, 1))
    home = italian.get("/").text
    assert "Cerca con i filtri" not in home and "Tutte le carte di Magic" not in home
    assert "<h2>Mappa della sintassi</h2>" in home and "<h3>Colori e mana</h3>" in home
    assert (
        'title="Possiedo proprio questa stampa (yes / no)"><dt>Possiedo questa stampa</dt>' in home
    )
    assert "<h2>Espansioni recenti</h2>" in home and "degli ultimi 5 anni" in home
    admin = italian.get("/admin").text
    assert "<h2>Pagina iniziale</h2>" in admin and ">Mappa della sintassi</button>" in admin
    italian.post("/admin/home", data={"view": "filters"})
    assert "<h2>Cerca con i filtri</h2>" in italian.get("/").text
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
