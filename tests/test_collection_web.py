"""Collection pages: adding copies, collection, locations, badges, Origin check (SPEC §7)."""

import re
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magicdb import db, i18n
from magicdb.app import create_app
from magicdb.collection import forms, service

UNKNOWN = "00000000-0000-4000-8000-000000000000"


@pytest.fixture
def client(catalog):
    return TestClient(create_app(catalog), follow_redirects=False)


@pytest.fixture
def ids(fixture_cards):
    def find(name, set_code):
        return next(c["id"] for c in fixture_cards if c["name"] == name and c["set"] == set_code)

    return find


@pytest.fixture
def places(client):
    for name, kind in [("Binder 1", "binder"), ("Blue box", "box")]:
        assert client.post("/locations", data={"name": name, "kind": kind}).status_code == 303
    return {"Binder 1": "1", "Blue box": "2"}


def rows(catalog):
    with closing(db.connect(catalog)) as conn:
        found = conn.execute(
            "SELECT e.name, e.set_code, e.quantity, e.finish, e.condition, e.language, l.name "
            "FROM entries e LEFT JOIN locations l ON l.id = e.location_id ORDER BY e.id"
        )
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
    menu = home.split('class="site-nav"')[1].split("</nav>")[0]
    assert 'href="/stats"' in menu and 'href="/decks"' in menu and menu.count("<a ") == 2


def test_name_suggestions_for_htmx(client):
    names = client.get("/add/names?name=light").text
    assert '<option value="Lightning Bolt">' in names and "<html" not in names
    assert client.get("/add/names?name=l").text == ""
    assert client.get("/add/names", params={"name": '"><script>'}).text == ""


def test_add_then_fields_stick(client, catalog, ids, places):
    bolt = ids("Lightning Bolt", "m10")
    response = add(
        client,
        bolt,
        quantity="2",
        finish="foil",
        condition="EX",
        language="it",
        location=places["Binder 1"],
    )
    assert (response.status_code, response.headers["location"]) == (303, f"/card/{bolt}#mine")
    assert f"{forms.STICKY_COOKIE}=" in response.headers["set-cookie"]
    assert rows(catalog) == [("Lightning Bolt", "m10", 2, "foil", "EX", "it", "Binder 1")]

    # every card page's add form is ready for the next copies: quantity 1, the rest as it was
    page = client.get(f"/card/{ids('Sol Ring', 'c21')}").text
    form = page.split('action="/add"')[1].split("</form>")[0]
    assert 'name="quantity" value="1"' in form
    assert [selected(form, f) for f in ("finish", "condition", "language", "location")] == [
        "foil",
        "EX",
        "it",
        places["Binder 1"],
    ]

    add(
        client,
        bolt,
        quantity="2",
        finish="foil",
        condition="EX",
        language="it",
        location=places["Binder 1"],
    )
    assert rows(catalog) == [("Lightning Bolt", "m10", 4, "foil", "EX", "it", "Binder 1")]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"quantity": "0"}, "whole number from 1 to 9999"),
        ({"quantity": "lots"}, "whole number from 1 to 9999"),
        ({"finish": "shiny"}, "That finish does not exist"),
        ({"condition": "MP"}, "That condition does not exist"),
        ({"language": "xx"}, "That language is not available"),
        ({"location": "99"}, "That location does not exist"),
        ({"location": "1; DROP TABLE entries"}, "That location does not exist"),
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


def test_card_page_add_form_and_my_copies(client, catalog, ids, places):
    m10, lea = ids("Lightning Bolt", "m10"), ids("Lightning Bolt", "lea")
    page = client.get(f"/card/{m10}").text
    assert "My copies" in page and "You do not own this card." in page
    assert "Add this printing (M10 #146)" in page
    assert f'name="printing" value="{m10}"' in page

    response = add(client, m10, quantity="3", location=places["Blue box"], next=f"/card/{m10}#mine")
    assert response.headers["location"] == f"/card/{m10}#mine"
    add(client, lea, condition="PL")

    page = client.get(f"/card/{m10}").text
    assert "You do not own this card." not in page
    mine = page.split('id="mine"')[1].split('class="printings"')[0]
    assert "Blue box" in mine and "No location" in mine
    assert f'href="/card/{lea}"' in mine  # the other printing I own is listed too
    assert 'href="/collection/1/edit?next=' in mine
    assert mine.count("<tr") == 2 and mine.count('class="current"') == 1
    assert selected(mine, "condition") == "PL"  # the last add's choices stick here as well


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
def stocked(client, ids, places):
    add(client, ids("Lightning Bolt", "m10"), quantity="3", location=places["Binder 1"])
    add(client, ids("Serra Angel", "dom"), quantity="4", finish="foil", location=places["Binder 1"])
    add(client, ids("Sol Ring", "c21"), quantity="2", condition="EX", language="it")
    return client


def test_empty_collection(client):
    page = client.get("/collection")
    assert page.status_code == 200 and "Your collection is empty" in page.text
    assert "Cards: 0 · Entries: 0" in page.text


def test_collection_lists_entries(stocked, ids):
    page = stocked.get("/collection").text
    assert "Cards: 9 · Entries: 3" in page
    for name in ("Lightning Bolt", "Serra Angel", "Sol Ring"):
        assert name in page
    assert f'href="/card/{ids("Sol Ring", "c21")}"' in page
    assert 'id="e1"' in page and 'hx-get="/collection/1/edit?next=/collection"' in page
    assert ">Foil</span>" in page and ">IT</span>" in page and "No location" in page
    assert "Shown —" not in page  # nothing is filtered


def test_collection_filters_and_sorts(stocked, places):
    binder = stocked.get(f"/collection?location={places['Binder 1']}").text
    assert "Sol Ring" not in binder and "Serra Angel" in binder
    assert "Shown — cards: 7 · entries: 2" in binder
    assert selected(binder, "location") == places["Binder 1"]
    loose = stocked.get("/collection?location=none").text
    assert "Sol Ring" in loose and "Serra Angel" not in loose
    named = stocked.get("/collection?q=bolt").text
    assert "Lightning Bolt" in named and "Sol Ring" not in named and 'value="bolt"' in named
    assert "Nothing in your collection matches" in stocked.get("/collection?q=zzz").text

    by_name = stocked.get("/collection?sort=name").text
    assert (
        by_name.index("Lightning Bolt") < by_name.index("Serra Angel") < by_name.index("Sol Ring")
    )
    assert selected(by_name, "sort") == "name"
    newest = stocked.get("/collection?sort=nonsense&location=abc&page=x").text
    assert newest.index("Sol Ring") < newest.index("Lightning Bolt")  # falls back to defaults


def test_collection_paging(stocked, monkeypatch):
    monkeypatch.setattr(service, "PER_PAGE", 2)
    first = stocked.get("/collection?sort=name").text
    assert "Page 1 of 2" in first and 'href="/collection?sort=name&amp;page=2"' in first
    assert "Sol Ring" not in first
    second = stocked.get("/collection?sort=name&page=2").text
    assert "Page 2 of 2" in second and "Sol Ring" in second and 'rel="prev"' in second


def test_collection_works_without_a_catalog(settings):
    client = TestClient(create_app(settings), follow_redirects=False)
    client.post("/locations", data={"name": "Box"})
    assert client.get("/collection").status_code == 200
    assert client.get("/locations").status_code == 200


# --- editing --------------------------------------------------------------------------


def test_inline_edit_form(stocked):
    inline = stocked.get(
        "/collection/1/edit?next=/collection?sort=name", headers={"HX-Request": "true"}
    )
    assert inline.status_code == 200
    assert inline.text.lstrip().startswith('<tr class="editing" id="e1">')
    assert "<html" not in inline.text
    assert 'action="/collection/1"' in inline.text
    assert 'name="next" value="/collection?sort=name"' in inline.text
    assert 'name="quantity" value="3"' in inline.text
    assert selected(inline.text, "finish") == "nonfoil" and selected(inline.text, "location") == "1"

    full = stocked.get("/collection/1/edit")
    assert "<html" in full.text and "Edit copies" in full.text and 'class="editing"' in full.text
    for missing in ("99", "abc", "1e3", "-1"):
        assert stocked.get(f"/collection/{missing}/edit").status_code == 404


def test_save_entry(stocked, catalog, places):
    response = stocked.post(
        "/collection/1",
        data={
            "quantity": "5",
            "finish": "foil",
            "condition": "GD",
            "language": "de",
            "location": places["Blue box"],
            "notes": "signed",
            "purchase_price": "2,50",
            "next": "/collection?sort=name#e1",
        },
    )
    assert (response.status_code, response.headers["location"]) == (303, "/collection?sort=name#e1")
    assert rows(catalog)[0] == ("Lightning Bolt", "m10", 5, "foil", "GD", "de", "Blue box")
    form = stocked.get("/collection/1/edit").text
    assert 'name="notes" value="signed"' in form and 'name="purchase_price" value="2.5"' in form


def test_save_into_a_collision_merges(stocked, catalog, ids, places):
    add(stocked, ids("Lightning Bolt", "m10"), quantity="1", location=places["Blue box"])  # entry 4
    response = stocked.post(
        "/collection/4",
        data={
            "quantity": "1",
            "finish": "nonfoil",
            "condition": "NM",
            "language": "en",
            "location": places["Binder 1"],
        },
    )
    assert response.headers["location"] == "/collection"
    bolts = [row for row in rows(catalog) if row[0] == "Lightning Bolt"]
    assert bolts == [("Lightning Bolt", "m10", 4, "nonfoil", "NM", "en", "Binder 1")]


def test_save_explains_bad_input(stocked, catalog):
    page = stocked.post(
        "/collection/1",
        data={
            "quantity": "0",
            "finish": "nonfoil",
            "condition": "NM",
            "language": "en",
            "location": "1",
            "next": "/collection?q=bolt",
        },
    )
    assert page.status_code == 400
    assert "whole number from 1 to 9999" in page.text and 'action="/collection/1"' in page.text
    assert 'name="next" value="/collection?q=bolt"' in page.text
    assert rows(catalog)[0][2] == 3


def test_move_entry(stocked, catalog, places):
    response = stocked.post(
        "/collection/1/move", data={"quantity": "2", "location": places["Blue box"]}
    )
    assert (response.status_code, response.headers["location"]) == (303, "/collection")
    bolts = sorted(row[2:] for row in rows(catalog) if row[0] == "Lightning Bolt")
    assert bolts == [(1, "nonfoil", "NM", "en", "Binder 1"), (2, "nonfoil", "NM", "en", "Blue box")]

    page = stocked.post("/collection/1/move", data={"quantity": "5", "location": ""})
    assert page.status_code == 400 and "You only have 1 of these." in page.text


def test_remove_entry(stocked, catalog):
    assert stocked.post("/collection/1/remove", data={"quantity": "1"}).status_code == 303
    assert rows(catalog)[0][2] == 2
    assert stocked.post("/collection/1/remove", data={}).status_code == 303  # all of them
    assert "Lightning Bolt" not in [row[0] for row in rows(catalog)]
    # the row is gone: a second click just goes back
    again = stocked.post("/collection/1/remove", data={"next": "/collection?sort=name"})
    assert (again.status_code, again.headers["location"]) == (303, "/collection?sort=name")


# --- locations ------------------------------------------------------------------------


def test_locations_page(client):
    empty = client.get("/locations")
    assert empty.status_code == 200 and "No locations yet" in empty.text
    assert (
        client.post("/locations", data={"name": " Raccoglitore  1 ", "kind": "binder"}).headers[
            "location"
        ]
        == "/locations"
    )
    page = client.get("/locations").text
    assert "<strong>Raccoglitore 1</strong>" in page and ">Binder</span>" in page
    assert "Cards: 0" in page and 'action="/locations/1/delete"' in page


def test_location_errors_are_explained(client, places):
    page = client.post("/locations", data={"name": "BINDER 1", "kind": "box"})
    assert page.status_code == 400
    assert "There is already a location called BINDER 1." in page.text
    assert "<strong>Binder 1</strong>" in page.text  # the list is still there
    assert client.post("/locations", data={"name": "", "kind": "box"}).status_code == 400
    assert client.post("/locations", data={"name": "x", "kind": "drawer"}).status_code == 400
    assert client.post("/locations/99", data={"name": "x", "kind": "box"}).status_code == 400
    assert client.post("/locations/abc/delete").status_code == 400


def test_rename_reorder_empty_delete(stocked, catalog, places):
    renamed = stocked.post("/locations/2", data={"name": "Red box", "kind": "box", "notes": "desk"})
    assert renamed.headers["location"] == "/locations#l2"
    stocked.post("/locations/2/shift", data={"offset": "-1"})
    page = stocked.get("/locations").text
    assert page.index("Red box") < page.index("Binder 1") and "desk" in page
    assert "Cards: 7" in page  # Binder 1
    assert 'href="/collection?location=none"' in page  # the loose Sol Rings
    assert 'action="/locations/1/empty"' in page and 'action="/locations/1/delete"' not in page

    refused = stocked.post("/locations/1/delete")
    assert refused.status_code == 400
    assert "Binder 1 still holds cards. Move them somewhere else first." in refused.text

    assert stocked.post("/locations/1/empty", data={"destination": "2"}).status_code == 303
    assert {row[6] for row in rows(catalog)} == {"Red box", None}
    assert stocked.post("/locations/1/delete").headers["location"] == "/locations"
    assert "<strong>Binder 1</strong>" not in stocked.get("/locations").text


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
def test_changes_from_another_site_are_refused(client, headers, expected):
    response = client.post("/locations", data={"name": "Box", "kind": "box"}, headers=headers)
    assert response.status_code == expected
    created = "<strong>Box</strong>" in client.get("/locations").text
    assert created == (expected == 303)


def test_reading_is_never_blocked_by_origin(client):
    assert client.get("/collection", headers={"Origin": "https://evil.example"}).status_code == 200


def test_every_change_is_a_post(client):
    for path in ("/locations/1/delete", "/collection/1/remove", "/collection/1/move"):
        assert client.get(path).status_code in (404, 405)


# --- translations ---------------------------------------------------------------------


def test_every_error_code_has_a_message():
    source = Path(service.__file__).read_text(encoding="utf-8")
    source += Path(forms.__file__).read_text(encoding="utf-8")
    codes = set(re.findall(r'CollectionError\(\s*"(\w+)"', source))
    assert len(codes) >= 12
    for lang in ("en", "it"):
        assert {f"error.{code}" for code in codes} <= set(i18n.strings(lang))


def test_every_choice_has_a_label():
    for lang in ("en", "it"):
        strings = i18n.strings(lang)
        for prefix, values in [
            ("finish", service.FINISHES),
            ("condition", service.CONDITIONS),
            ("lang", service.LANGUAGES),
            ("kind", service.KINDS),
            ("collection.sort", service.SORTS),
        ]:
            assert {f"{prefix}.{value}" for value in values} <= set(strings)


def test_italian_collection_pages(client, ids):
    client.get("/lang/it")
    assert "La tua collezione è vuota" in client.get("/collection").text
    page = add(client, ids("Sol Ring", "c21"), quantity="0")
    assert "La quantità deve essere un numero intero" in page.text
    assert "Aggiungi questa stampa (C21 #" in page.text
    done = add(client, ids("Sol Ring", "c21"), quantity="2")
    mine = client.get(done.headers["location"]).text.split('id="mine"')[1]
    assert "Senza posizione" in mine and "Non possiedi questa carta." not in mine
