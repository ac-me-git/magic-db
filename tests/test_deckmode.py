"""Deck mode: + and − under the cards of a search fill a deck; switches beside the
pickers of deck mode and fast input limit the search to that deck or location (SPEC §7)."""

import re
from contextlib import closing
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from magicdb import db, search
from magicdb.app import create_app

HTMX = {"HX-Request": "true"}


@pytest.fixture
def client(catalog):
    return TestClient(create_app(catalog), follow_redirects=False)


@pytest.fixture
def card(fixture_cards):
    """A paper card with a plain name and more than one printing."""
    names = [c["name"] for c in fixture_cards if not c["digital"] and "//" not in c["name"]]
    name = next(n for n in names if names.count(n) > 1)
    return next(c for c in fixture_cards if c["name"] == name and not c["digital"])


@pytest.fixture
def other(fixture_cards, card):
    return next(
        c
        for c in fixture_cards
        if c["oracle_id"] != card["oracle_id"] and not c["digital"] and "//" not in c["name"]
    )


def make_deck(client, name):
    answer = client.post("/decks", data={"name": name})
    assert answer.status_code == 303
    return int(answer.headers["location"].rsplit("/", 1)[1])


def deck_cards(catalog):
    with closing(db.connect(catalog)) as conn:
        rows = conn.execute(
            "SELECT deck_id, oracle_id, board, quantity FROM deck_cards ORDER BY id"
        )
        return [tuple(row) for row in rows]


def click(client, deck, card, action="add", board="main", **extra):
    data = {"deck": deck, "printing": card["id"], "board": board, "action": action, **extra}
    return client.post("/deckmode/card", data=data, headers=HTMX)


def shown(html):
    """The number between − and + in a click's answer or in the first card of a page."""
    return int(re.search(r'<output class="deck-n[^"]*"[^>]*>(\d+)</output>', html).group(1))


def search_page(client, q, view="deck"):
    return client.get("/search", params={"q": q, "view": view}).text


def query_of(address):
    return parse_qs(urlsplit(address).query).get("q", [""])[0]


# --- the view -------------------------------------------------------------------------


def test_results_offer_deck_mode_as_a_fourth_view(client):
    page = client.get("/search?q=bolt").text
    assert ">Deck</a>" in page and "view=deck" in page
    assert "deck-count" not in page  # the buttons are only in that view


def test_without_decks_the_view_says_so_and_links_to_the_decks(client):
    page = search_page(client, "bolt")
    assert "You have no decks yet" in page and 'href="/decks"' in page
    assert "deck-count" not in page and 'action="/deckmode/settings"' not in page


def test_the_view_shows_the_deck_picker_the_board_and_buttons_per_card(client, card):
    make_deck(client, "Humans")
    page = search_page(client, f'!"{card["name"]}" unique:prints')
    assert 'action="/deckmode/settings"' in page and ">Humans</option>" in page
    assert ">Main deck</option>" in page and ">Sideboard</option>" in page
    # a deck lists cards, not printings: one image per card whatever unique: says
    assert page.count('class="deck-count"') == 1
    assert shown(page) == 0
    assert re.search(r'value="remove" class="deck-step"[^>]*disabled', page)  # nothing to take


def test_plus_and_minus_change_the_copies_in_the_deck(client, catalog, card):
    deck = make_deck(client, "Humans")
    assert shown(click(client, deck, card).text) == 1
    assert shown(click(client, deck, card).text) == 2
    assert deck_cards(catalog) == [(deck, card["oracle_id"], "main", 2)]
    assert shown(click(client, deck, card, "remove").text) == 1
    assert shown(click(client, deck, card, "remove").text) == 0
    assert deck_cards(catalog) == []  # the last copy takes the row away
    assert shown(click(client, deck, card, "remove").text) == 0  # and nothing below zero


def test_any_printing_counts_as_the_same_card(client, catalog, card, fixture_cards):
    deck = make_deck(client, "Humans")
    twins = [c for c in fixture_cards if c["oracle_id"] == card["oracle_id"]]
    for printing in twins[:2]:
        click(client, deck, printing)
    assert deck_cards(catalog) == [(deck, card["oracle_id"], "main", 2)]


def test_the_sideboard_is_counted_apart(client, catalog, card):
    deck = make_deck(client, "Humans")
    click(client, deck, card)
    assert shown(click(client, deck, card, board="side").text) == 1
    assert sorted(deck_cards(catalog)) == [
        (deck, card["oracle_id"], "main", 1),
        (deck, card["oracle_id"], "side", 1),
    ]
    client.get("/deckmode/settings", params={"deck": deck, "board": "side"})
    page = search_page(client, f'!"{card["name"]}"')
    assert 'name="board" value="side"' in page and shown(page) == 1


def test_without_htmx_a_click_goes_back_to_the_search(client, card):
    deck = make_deck(client, "Humans")
    here = "/search?q=bolt&view=deck"
    data = {"deck": deck, "printing": card["id"], "board": "main", "next": here}
    answer = client.post("/deckmode/card", data=data)
    assert answer.status_code == 303 and answer.headers["location"] == here
    data["next"] = "//evil.example"
    assert client.post("/deckmode/card", data=data).headers["location"] == "/"


@pytest.mark.parametrize(
    "change",
    [
        {"printing": "00000000-0000-4000-8000-000000000000"},
        {"printing": "nonsense"},
        {"board": "maybe"},
        {"action": "twice"},
        {"deck": "999"},
        {"deck": "x"},
    ],
)
def test_nonsense_is_not_found_and_changes_nothing(client, catalog, card, change):
    deck = make_deck(client, "Humans")
    data = {"deck": deck, "printing": card["id"], "board": "main", "action": "add", **change}
    assert client.post("/deckmode/card", data=data, headers=HTMX).status_code == 404
    assert deck_cards(catalog) == []


def test_locked_collection_shows_the_buttons_disabled_and_refuses_clicks(client, catalog, card):
    deck = make_deck(client, "Humans")
    click(client, deck, card)
    client.post("/lock", data={"state": "on"})
    page = search_page(client, f'!"{card["name"]}"')
    assert "locked-notice" in page
    assert len(re.findall(r'class="deck-step"[^>]*disabled', page)) == 2
    assert click(client, deck, card).status_code == 423
    assert deck_cards(catalog) == [(deck, card["oracle_id"], "main", 1)]
    # choosing the deck is only a cookie: allowed
    assert client.get("/deckmode/settings", params={"deck": deck}).status_code == 303


# --- which deck -----------------------------------------------------------------------


def test_the_deck_changed_last_is_chosen_until_another_is_picked(client, catalog, card):
    first = make_deck(client, "Angels")
    make_deck(client, "Zombies")
    with closing(db.connect(catalog)) as conn, conn:  # Angels changed last
        conn.execute("UPDATE decks SET updated_at = ? WHERE id = ?", ("2999-01-01", first))
    page = search_page(client, f'!"{card["name"]}"')
    assert f'<option value="{first}" selected>Angels</option>' in page
    second = make_deck(client, "Humans")
    client.get("/deckmode/settings", params={"deck": second})
    page = search_page(client, f'!"{card["name"]}"')
    assert f'<option value="{second}" selected>Humans</option>' in page
    assert f'name="deck" value="{second}"' in page


def test_a_deck_that_was_deleted_falls_back_to_another(client, card):
    keep = make_deck(client, "Keep")
    gone = make_deck(client, "Gone")
    client.get("/deckmode/settings", params={"deck": gone, "board": "side"})
    client.post(f"/decks/{gone}/delete")
    page = search_page(client, f'!"{card["name"]}"')
    assert f'<option value="{keep}" selected>Keep</option>' in page
    assert 'name="board" value="side"' in page  # the board is still remembered


def test_settings_ignore_nonsense(client, card):
    deck = make_deck(client, "Humans")
    client.get("/deckmode/settings", params={"deck": "abc", "board": "maybe"})
    page = search_page(client, f'!"{card["name"]}"')
    assert f'name="deck" value="{deck}"' in page and 'name="board" value="main"' in page
    assert (
        client.get("/deckmode/settings", params={"next": "//evil.example"}).headers["location"]
        == "/"
    )


# --- the deck switch ------------------------------------------------------------------


def only(page):
    """(is on, the address it leads to) of the switch beside the picker."""
    found = re.search(
        r'<span class="only-switch">\s*<a href="([^"]+)" class="switch[^"]*" role="switch"\s*'
        r'aria-checked="(\w+)"',
        page,
    )
    return found.group(2) == "true", found.group(1).replace("&amp;", "&")


def test_the_deck_switch_writes_the_deck_filter_into_the_search(client, card, other):
    deck = make_deck(client, "Human Army")
    click(client, deck, card)
    on, address = only(search_page(client, "mv>=0"))
    assert not on and query_of(address) == 'mv>=0 deck:"Human Army"'
    assert "view=deck" in address
    page = client.get(address).text
    on, back = only(page)
    assert on and query_of(back) == "mv>=0"
    # only the cards already in the deck
    assert page.count('class="deck-count"') == 1 and card["name"] in page


def test_the_deck_switch_works_with_the_collection_switch(client, catalog, card, other):
    deck = make_deck(client, "Humans")
    click(client, deck, card)
    click(client, deck, other)
    client.post("/add", data={"printing": card["id"]})
    q = f'deck:"Humans" {search.COLLECTION_TERM}'
    page = search_page(client, q)
    assert only(page)[0] and page.count('class="deck-count"') == 1
    assert f'value="{card["id"]}"' in page


def test_picking_another_deck_moves_the_switch_to_it(client):
    first = make_deck(client, "Humans")
    second = make_deck(client, "Elves")
    client.get("/deckmode/settings", params={"deck": first})
    here = "/search?" + "&".join(["q=t%3Acreature+deck%3A%22Humans%22", "view=deck"])
    answer = client.get("/deckmode/settings", params={"deck": second, "next": here})
    assert answer.status_code == 303
    assert query_of(answer.headers["location"]) == 't:creature deck:"Elves"'
    # with HTMX the whole page loads, so that the search box shows the new search
    client.get("/deckmode/settings", params={"deck": first})
    answer = client.get("/deckmode/settings", params={"deck": second, "next": here}, headers=HTMX)
    assert answer.status_code == 204
    assert query_of(answer.headers["HX-Redirect"]) == 't:creature deck:"Elves"'


def test_picking_another_deck_leaves_a_search_without_the_filter_alone(client):
    first = make_deck(client, "Humans")
    second = make_deck(client, "Elves")
    client.get("/deckmode/settings", params={"deck": first})
    here = "/search?q=t%3Acreature&view=deck"
    answer = client.get("/deckmode/settings", params={"deck": second, "next": here}, headers=HTMX)
    assert answer.status_code == 303 and answer.headers["location"] == here


# --- the location switch in fast input ------------------------------------------------


def test_fast_input_has_a_location_switch_once_a_location_is_chosen(client, card):
    page = search_page(client, f"set:{card['set']}", view="input")
    assert "only-switch" not in page  # new copies go to no location: nothing to limit to
    client.post("/locations", data={"name": "Binder A", "kind": "binder"})
    client.get("/fast/settings", params={"location": "1"})
    on, address = only(search_page(client, f"set:{card['set']}", view="input"))
    assert not on and query_of(address) == f'set:{card["set"]} loc:"Binder A"'
    assert "view=input" in address
    on, back = only(client.get(address).text)
    assert on and query_of(back) == f"set:{card['set']}"


def test_with_nothing_found_the_switches_stay_so_they_can_be_turned_off(client):
    make_deck(client, "Empty")
    on, back = only(search_page(client, 'deck:"Empty"'))
    assert on and query_of(back) == ""
    client.post("/locations", data={"name": "Bare", "kind": "box"})
    client.get("/fast/settings", params={"location": "1"})
    on, back = only(search_page(client, 'loc:"Bare"', view="input"))
    assert on and query_of(back) == ""


def test_picking_another_location_moves_the_switch_to_it(client):
    client.post("/locations", data={"name": "Binder A", "kind": "binder"})
    client.post("/locations", data={"name": "Box B", "kind": "box"})
    client.get("/fast/settings", params={"location": "1"})
    here = "/search?q=set%3Aabc+loc%3A%22Binder+A%22&view=input"
    answer = client.get("/fast/settings", params={"location": "2", "next": here})
    assert query_of(answer.headers["location"]) == 'set:abc loc:"Box B"'


# --- the search helpers ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ('deck:"Humans"', True),
        ("deck:humans", True),
        ('t:elf deck:"HUMANS"', True),
        ('deck:"Human"', False),  # another deck
        ('-deck:"Humans"', False),
        ('(t:elf deck:"Humans")', False),
        ('t:elf or deck:"Humans"', False),
        ('deck="Humans"', False),
        ('deck:"Humans', False),  # cannot be read
    ],
)
def test_has_named(query, expected):
    assert search.has_named(query, "deck", "Humans") is expected


def test_with_named_adds_and_takes_out_the_term():
    assert search.with_named("t:elf", "deck", "Humans", True) == 't:elf deck:"Humans"'
    assert search.with_named("", "deck", "Humans", True) == 'deck:"Humans"'
    assert search.with_named("t:elf or t:goblin", "loc", "Box", True) == (
        '(t:elf or t:goblin) loc:"Box"'
    )
    assert search.with_named('t:elf deck:"Humans" c:w', "deck", "Humans", False) == "t:elf c:w"
    assert search.with_named('location:"box" t:elf', "loc", "Box", False) == "t:elf"
    assert search.with_named('t:elf deck:"Humans"', "deck", "Humans", True) == (
        't:elf deck:"Humans"'
    )
    assert search.with_named("t:elf", "deck", "", True) == "t:elf"


def test_swap_named_only_moves_a_term_that_is_there():
    assert search.swap_named('t:elf deck:"A"', "deck", "A", "B") == 't:elf deck:"B"'
    assert search.swap_named("t:elf", "deck", "A", "B") == "t:elf"
    assert search.swap_named('t:elf deck:"C"', "deck", "A", "B") == 't:elf deck:"C"'


def test_the_collection_switch_still_works(client):
    assert search.with_collection("t:elf", True) == f"t:elf {search.COLLECTION_TERM}"
    assert search.with_collection(f"t:elf {search.COLLECTION_TERM}", False) == "t:elf"
    assert search.in_collection(f"(t:elf) {search.COLLECTION_TERM}")
