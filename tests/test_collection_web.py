"""Collection pages: adding and editing copies, badges, Origin check (SPEC §7)."""

import re
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magicdb import db, i18n
from magicdb.app import create_app
from magicdb.collection import forms, service
from magicdb.templating import STATIC_DIR as STATIC

UNKNOWN = "00000000-0000-4000-8000-000000000000"


@pytest.fixture
def client(catalog):
    return TestClient(create_app(catalog), follow_redirects=False)


@pytest.fixture
def ids(fixture_cards):
    def find(name, set_code):
        return next(c["id"] for c in fixture_cards if c["name"] == name and c["set"] == set_code)

    return find


def rows(catalog):
    with closing(db.connect(catalog)) as conn:
        found = conn.execute("SELECT name, set_code, quantity, finish FROM entries ORDER BY id")
        return [tuple(row) for row in found]


def add(client, printing, **data):
    return client.post("/add", data={"printing": printing, **data})


def selected(html, field):
    """The value of the option selected in a <select name=field>."""
    block = re.search(rf'<select name="{field}".*?</select>', html, re.S).group(0)
    match = re.search(r'<option value="([^"]*)" selected', block)
    return match.group(1) if match else ""


# --- adding copies --------------------------------------------------------------------


def test_navigation_links(client):
    home = client.get("/").text
    menu = home.split('<div class="menu-panel">')[1].split("</details>")[0]
    assert 'href="/stats"' in menu and 'href="/decks"' in menu
    assert menu.count('<a class="menu-link"') == 4  # and the syntax, and Administration


def test_name_suggestions_for_htmx(client):
    names = client.get("/add/names?name=light").text
    assert '<option value="Lightning Bolt">' in names and "<html" not in names
    assert client.get("/add/names?name=l").text == ""
    assert client.get("/add/names", params={"name": '"><script>'}).text == ""


def test_add_then_the_finish_sticks(client, catalog, ids):
    bolt = ids("Lightning Bolt", "m10")
    response = add(client, bolt, quantity="2", finish="foil")
    assert (response.status_code, response.headers["location"]) == (303, f"/card/{bolt}#mine")
    assert f"{forms.STICKY_COOKIE}=" in response.headers["set-cookie"]
    assert rows(catalog) == [("Lightning Bolt", "m10", 2, "foil")]

    # every card page's add form is ready for the next copies: quantity 1, the same finish,
    # and nothing to say about condition, language or place
    page = client.get(f"/card/{ids('Sol Ring', 'c21')}").text
    form = page.split('action="/add"')[1].split("</form>")[0]
    assert 'name="quantity" value="1"' in form and selected(form, "finish") == "foil"
    for gone in ("condition", "language", "location"):
        assert f'name="{gone}"' not in form

    add(client, bolt, quantity="2", finish="foil")
    assert rows(catalog) == [("Lightning Bolt", "m10", 4, "foil")]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"quantity": "0"}, "whole number from 1 to 9999"),
        ({"quantity": "lots"}, "whole number from 1 to 9999"),
        ({"finish": "shiny"}, "That finish does not exist"),
        ({"finish": "foil'; DROP TABLE entries"}, "That finish does not exist"),
    ],
)
def test_add_explains_bad_input_and_adds_nothing(client, catalog, ids, data, message):
    bolt = ids("Lightning Bolt", "m10")
    page = add(client, bolt, **data)
    assert page.status_code == 400
    form = page.text.split('action="/add"')[1].split("</form>")[0]  # the card page's form
    assert message in form and f'name="printing" value="{bolt}"' in form
    assert rows(catalog) == []


def test_only_printings_from_the_catalog_can_be_added(client, catalog):
    for printing in ("", UNKNOWN, "nonsense", "../../etc"):
        assert add(client, printing).status_code == 404, printing
        assert add(client, printing, quantity="0").status_code == 404, printing
    assert rows(catalog) == []


def test_there_is_no_add_cards_page(client):
    for path in ("/add", "/add?name=Lightning%20Bolt", "/add/printings", "/add/preview"):
        assert client.get(path).status_code in (404, 405), path


def test_unusual_finish_is_still_added(client, catalog, ids):
    response = add(client, ids("Lightning Bolt", "lea"), finish="foil")
    assert response.status_code == 303
    assert rows(catalog)[0][3] == "foil"


def test_adding_needs_the_catalog(settings):
    client = TestClient(create_app(settings))
    assert client.post("/add", data={"printing": UNKNOWN}).status_code == 503


# --- card page ------------------------------------------------------------------------


def test_card_page_add_form_and_my_copies(client, catalog, ids):
    m10, lea = ids("Lightning Bolt", "m10"), ids("Lightning Bolt", "lea")
    page = client.get(f"/card/{m10}").text
    assert "My copies" in page and "You do not own this card." in page
    assert "Add this printing (M10 #146)" in page
    assert f'name="printing" value="{m10}"' in page

    response = add(client, m10, quantity="3", next=f"/card/{m10}#mine")
    assert response.headers["location"] == f"/card/{m10}#mine"
    add(client, lea, finish="foil")

    page = client.get(f"/card/{m10}").text
    assert "You do not own this card." not in page
    mine = page.split('id="mine"')[1].split('class="printings"')[0]
    assert ">Foil</span>" in mine and "No location" not in mine
    assert f'href="/card/{lea}"' in mine  # the other printing I own is listed too
    assert 'href="/collection/1/edit?next=' in mine
    assert mine.count("<tr") == 2 and mine.count('class="current"') == 1
    assert selected(mine, "finish") == "foil"  # the last add's finish sticks here as well


def test_redirect_after_add_stays_on_this_site(client, ids):
    sol = ids("Sol Ring", "c21")
    response = add(client, sol, next="https://evil.example/")
    assert response.headers["location"] == f"/card/{sol}#mine"


# --- badges in search results ---------------------------------------------------------


def test_search_results_show_what_i_own(client, ids):
    add(client, ids("Lightning Bolt", "m10"), quantity="3")
    add(client, ids("Lightning Bolt", "lea"), quantity="1")

    cards = client.get('/search?q=!"Lightning Bolt"').text
    assert '<span class="owned" title="You own 4">×4</span>' in cards  # every printing counts
    # on the picture, in a box of its own that puts it in the bottom left corner: the
    # top right is where the mana cost is
    assert re.search(r'<span class="card-face">\s*<img [^>]+><span class="owned"', cards)
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    rule = css.split(".card-grid .owned {")[1].split("}")[0]
    assert "bottom:" in rule and "left:" in rule and "top:" not in rule
    prints = client.get('/search?q=!"Lightning Bolt" unique:prints').text
    assert prints.count('class="owned"') == 2
    assert "×3</span>" in prints and "×1</span>" in prints
    listed = client.get('/search?q=!"Lightning Bolt"&view=list').text
    assert "×4</span>" in listed
    assert 'class="owned"' not in client.get("/search?q=t:angel").text
    partial = client.get('/search?q=!"Lightning Bolt"', headers={"HX-Request": "true"}).text
    assert "×4</span>" in partial


# --- the collection page --------------------------------------------------------------


@pytest.fixture
def stocked(client, ids):
    add(client, ids("Lightning Bolt", "m10"), quantity="3")
    add(client, ids("Serra Angel", "dom"), quantity="4", finish="foil")
    add(client, ids("Sol Ring", "c21"), quantity="2")
    return client


def test_there_is_no_card_list_page(stocked):
    # the collection is searched like every card: haveprint:yes, qty …
    assert stocked.get("/collection").status_code == 404
    for path in ("/", "/stats", "/admin", "/import"):
        assert 'href="/collection"' not in stocked.get(path).text, path


def test_there_are_no_locations(stocked):
    for path in ("/locations", "/locations/1", "/fast/settings"):
        assert stocked.get(path).status_code in (404, 405), path
    for path in ("/locations", "/locations/1/delete", "/collection/1/move"):
        assert stocked.post(path, data={"name": "Box"}).status_code in (404, 405), path
    for path in ("/", "/stats", "/admin", "/import", "/export"):
        assert 'href="/locations"' not in stocked.get(path).text, path


# --- editing --------------------------------------------------------------------------


def test_edit_form(stocked):
    full = stocked.get("/collection/1/edit?next=/stats")
    assert "<html" in full.text and "Edit copies" in full.text and 'class="editing"' in full.text
    assert 'action="/collection/1"' in full.text
    assert 'name="next" value="/stats"' in full.text
    assert 'name="quantity" value="3"' in full.text
    assert selected(full.text, "finish") == "nonfoil"
    assert "/move" not in full.text and 'name="location"' not in full.text
    # a whole page even when htmx asks: only the card list asked for a bare row
    assert "<html" in stocked.get("/collection/1/edit", headers={"HX-Request": "true"}).text
    for missing in ("99", "abc", "1e3", "-1"):
        assert stocked.get(f"/collection/{missing}/edit").status_code == 404


def test_save_entry(stocked, catalog):
    response = stocked.post(
        "/collection/1",
        data={
            "quantity": "5",
            "finish": "foil",
            "notes": "signed",
            "purchase_price": "2,50",
            "next": "/stats#sets",
        },
    )
    assert (response.status_code, response.headers["location"]) == (303, "/stats#sets")
    assert rows(catalog)[0] == ("Lightning Bolt", "m10", 5, "foil")
    form = stocked.get("/collection/1/edit").text
    assert 'name="notes" value="signed"' in form and 'name="purchase_price" value="2.5"' in form


def test_save_into_a_collision_merges(stocked, catalog, ids):
    add(stocked, ids("Lightning Bolt", "m10"), quantity="1", finish="foil")  # entry 4
    response = stocked.post("/collection/4", data={"quantity": "1", "finish": "nonfoil"})
    assert response.headers["location"] == "/stats"  # no next: the collection's page
    bolts = [row for row in rows(catalog) if row[0] == "Lightning Bolt"]
    assert bolts == [("Lightning Bolt", "m10", 4, "nonfoil")]


def test_save_explains_bad_input(stocked, catalog):
    page = stocked.post(
        "/collection/1",
        data={"quantity": "0", "finish": "nonfoil", "next": "/stats"},
    )
    assert page.status_code == 400
    assert "whole number from 1 to 9999" in page.text and 'action="/collection/1"' in page.text
    assert 'name="next" value="/stats"' in page.text
    assert rows(catalog)[0][2] == 3


def test_remove_entry(stocked, catalog):
    assert stocked.post("/collection/1/remove", data={"quantity": "1"}).status_code == 303
    assert rows(catalog)[0][2] == 2
    assert stocked.post("/collection/1/remove", data={}).status_code == 303  # all of them
    assert "Lightning Bolt" not in [row[0] for row in rows(catalog)]
    # the row is gone: a second click just goes back
    again = stocked.post("/collection/1/remove", data={"next": "/stats"})
    assert (again.status_code, again.headers["location"]) == (303, "/stats")


# --- cross-site requests --------------------------------------------------------------


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({}, 303),  # no Origin: not a browser form from another site
        ({"Origin": "http://testserver"}, 303),
        ({"Origin": "https://testserver"}, 303),
        ({"Origin": "https://cards.example", "X-Forwarded-Host": "cards.example"}, 303),
        ({"Origin": "https://evil.example"}, 403),
        ({"Origin": "http://testserver.evil.example"}, 403),
        ({"Origin": "http://testserver:8080"}, 403),
        ({"Origin": "null"}, 403),
    ],
)
def test_changes_from_another_site_are_refused(client, catalog, ids, headers, expected):
    data = {"printing": ids("Sol Ring", "c21")}
    response = client.post("/add", data=data, headers=headers)
    assert response.status_code == expected
    assert bool(rows(catalog)) == (expected == 303)


def test_reading_is_never_blocked_by_origin(client):
    assert client.get("/stats", headers={"Origin": "https://evil.example"}).status_code == 200


def test_every_change_is_a_post(client):
    for path in ("/collection/1/remove", "/fast/toggle", "/deckmode/card"):
        assert client.get(path).status_code in (404, 405)


# --- translations ---------------------------------------------------------------------


def test_every_error_code_has_a_message():
    source = Path(service.__file__).read_text(encoding="utf-8")
    source += Path(forms.__file__).read_text(encoding="utf-8")
    codes = set(re.findall(r'CollectionError\(\s*"(\w+)"', source))
    assert len(codes) >= 6
    for lang in ("en", "it"):
        assert {f"error.{code}" for code in codes} <= set(i18n.strings(lang))


def test_every_choice_has_a_label():
    for lang in ("en", "it"):
        strings = i18n.strings(lang)
        assert {f"finish.{value}" for value in service.FINISHES} <= set(strings)


def test_italian_collection_pages(client, ids):
    client.get("/lang/it")
    assert '<h1 class="page-title">La mia collezione</h1>' in client.get("/stats").text
    page = add(client, ids("Sol Ring", "c21"), quantity="0")
    assert "La quantità deve essere un numero intero" in page.text
    assert "Aggiungi questa stampa (C21 #" in page.text
    done = add(client, ids("Sol Ring", "c21"), quantity="2")
    mine = client.get(done.headers["location"]).text.split('id="mine"')[1]
    assert "Non possiedi questa carta." not in mine
