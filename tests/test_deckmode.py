"""Deck mode: + and − on the cards of a search fill a deck; a switch beside the deck
picker limits the search to that deck (SPEC §7)."""

import re
from contextlib import closing
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from magicdb import db, search
from magicdb.app import create_app
from tests.conftest import editing

HTMX = {"HX-Request": "true"}


@pytest.fixture
def client(catalog):
    client = TestClient(create_app(catalog), follow_redirects=False)
    client.post("/mode", data={"state": "deck"})  # what these tests change
    return client


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
        rows = conn.execute("SELECT deck_id, oracle_id, quantity FROM deck_cards ORDER BY id")
        return [tuple(row) for row in rows]


def click(client, deck, card, action="add", **extra):
    data = {"deck": deck, "printing": card["id"], "action": action, **extra}
    return client.post("/deckmode/card", data=data, headers=HTMX)


def shown(html):
    """The number between − and + in a click's answer or in the first card of a page."""
    return int(re.search(r'<output class="deck-n[^"]*"[^>]*>(\d+)</output>', html).group(1))


def search_page(client, q, view="grid"):
    return client.get("/search", params={"q": q, "view": view}).text


def query_of(address):
    return parse_qs(urlsplit(address).query).get("q", [""])[0]


# --- the view -------------------------------------------------------------------------


def test_deck_mode_follows_the_switch(client):
    make_deck(client, "Humans")
    page = client.get("/search?q=bolt").text
    assert ">Deck building</span></button>" in page and "deck-count" in page
    assert re.search(r'value="deck" aria-pressed="true"', page)
    editing(client, "locked")
    page = client.get("/search?q=bolt").text
    assert "deck-count" not in page  # the buttons are only there while building decks
    assert re.search(r'value="locked" aria-pressed="true"', page)


def test_without_decks_the_view_says_so_and_links_to_the_decks(client):
    page = search_page(client, "bolt")
    assert "You have no decks yet" in page and 'href="/decks"' in page
    assert "deck-count" not in page and 'action="/deckmode/settings"' not in page


def test_the_view_shows_the_deck_picker_and_buttons_per_card(client, card):
    make_deck(client, "Humans")
    page = search_page(client, f'!"{card["name"]}" unique:prints')
    assert 'action="/deckmode/settings"' in page and ">Humans</option>" in page
    assert 'name="board"' not in page and "Sideboard" not in page  # a deck is one list
    # a deck lists cards, not printings: one image per card whatever unique: says
    assert page.count('class="deck-count"') == 1
    assert shown(page) == 0
    assert re.search(r'value="remove" class="deck-step"[^>]*disabled', page)  # nothing to take


def test_the_explanation_is_behind_an_info_button(client, card):
    make_deck(client, "Humans")
    page = search_page(client, "bolt")
    info = page.split('<details class="info">', 1)[1].split("</details>", 1)[0]
    assert "Press + to put one more copy" in info
    assert page.count("Press + to put one more copy") == 1


def test_plus_and_minus_change_the_copies_in_the_deck(client, catalog, card):
    deck = make_deck(client, "Humans")
    assert shown(click(client, deck, card).text) == 1
    assert shown(click(client, deck, card).text) == 2
    assert deck_cards(catalog) == [(deck, card["oracle_id"], 2)]
    assert shown(click(client, deck, card, "remove").text) == 1
    assert shown(click(client, deck, card, "remove").text) == 0
    assert deck_cards(catalog) == []  # the last copy takes the row away
    assert shown(click(client, deck, card, "remove").text) == 0  # and nothing below zero


def test_any_printing_counts_as_the_same_card(client, catalog, card, fixture_cards):
    deck = make_deck(client, "Humans")
    twins = [c for c in fixture_cards if c["oracle_id"] == card["oracle_id"]]
    for printing in twins[:2]:
        click(client, deck, printing)
    assert deck_cards(catalog) == [(deck, card["oracle_id"], 2)]


def test_without_htmx_a_click_goes_back_to_the_search(client, card):
    deck = make_deck(client, "Humans")
    here = "/search?q=bolt&view=deck"
    data = {"deck": deck, "printing": card["id"], "next": here}
    answer = client.post("/deckmode/card", data=data)
    assert answer.status_code == 303 and answer.headers["location"] == here
    data["next"] = "//evil.example"
    assert client.post("/deckmode/card", data=data).headers["location"] == "/"


@pytest.mark.parametrize(
    "change",
    [
        {"printing": "00000000-0000-4000-8000-000000000000"},
        {"printing": "nonsense"},
        {"action": "twice"},
        {"deck": "999"},
        {"deck": "x"},
    ],
)
def test_nonsense_is_not_found_and_changes_nothing(client, catalog, card, change):
    deck = make_deck(client, "Humans")
    data = {"deck": deck, "printing": card["id"], "action": "add", **change}
    assert client.post("/deckmode/card", data=data, headers=HTMX).status_code == 404
    assert deck_cards(catalog) == []


@pytest.mark.parametrize("state", ["locked", "input"])
def test_out_of_deck_building_the_buttons_go_and_clicks_are_refused(client, catalog, card, state):
    deck = make_deck(client, "Humans")
    click(client, deck, card)
    editing(client, state)
    page = search_page(client, f'!"{card["name"]}"')
    assert "deck-count" not in page and "locked-notice" not in page
    assert click(client, deck, card).status_code == 423
    assert deck_cards(catalog) == [(deck, card["oracle_id"], 1)]
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
    client.get("/deckmode/settings", params={"deck": gone})
    client.post(f"/decks/{gone}/delete")
    page = search_page(client, f'!"{card["name"]}"')
    assert f'<option value="{keep}" selected>Keep</option>' in page


def test_settings_ignore_nonsense(client, card):
    deck = make_deck(client, "Humans")
    client.get("/deckmode/settings", params={"deck": "abc"})
    page = search_page(client, f'!"{card["name"]}"')
    assert f'name="deck" value="{deck}"' in page
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
    page = client.get(address).text
    on, back = only(page)
    assert on and query_of(back) == "mv>=0"
    # only the cards already in the deck
    assert page.count('class="deck-count"') == 1 and card["name"] in page


def test_the_deck_switch_works_with_the_collection_switch(client, catalog, card, other):
    deck = make_deck(client, "Humans")
    click(client, deck, card)
    click(client, deck, other)
    editing(client, "input").post("/add", data={"printing": card["id"]})
    editing(client, "deck")
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


def test_with_nothing_found_the_switch_stays_so_it_can_be_turned_off(client):
    make_deck(client, "Empty")
    on, back = only(search_page(client, 'deck:"Empty"'))
    assert on and query_of(back) == ""


def test_fast_input_has_no_switch_and_no_pickers(client, card):
    page = search_page(client, f"set:{card['set']}", view="input")
    assert "only-switch" not in page and "<select" not in page.split("fast-list")[0]


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
    assert search.with_named("t:elf or t:goblin", "deck", "Box", True) == (
        '(t:elf or t:goblin) deck:"Box"'
    )
    assert search.with_named('t:elf deck:"Humans" c:w', "deck", "Humans", False) == "t:elf c:w"
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


# --- both modes in both layouts -------------------------------------------------------


def views_links(page):
    """{label: (view in the address, is current)} of the links above the results."""
    bar = page.split('<div class="views">')[1].split("</div>")[0]
    found = {}
    for tag in re.findall(r"<a [^>]*>", bar):
        label = re.search(r'aria-label="([^"]+)"', tag).group(1)
        href = re.search(r'href="([^"]+)"', tag).group(1).replace("&amp;", "&")
        view = parse_qs(urlsplit(href).query).get("view", ["grid"])[0]
        found[label] = (view, 'aria-current="true"' in tag)
    return found


def test_images_or_list_is_a_slider_that_keeps_the_mode(client):
    make_deck(client, "Humans")
    images = search_page(client, "bolt", view="grid")
    assert views_links(images) == {"Images": ("grid", True), "List": ("list", False)}
    assert '<span class="slider layout-switch">' in images
    listed = search_page(client, "bolt", view="list")
    assert views_links(listed) == {"Images": ("grid", False), "List": ("list", True)}
    assert "deck-list" in listed  # the mode comes from the switch, not the address
    # addresses saved when the view also chose the mode keep their layout
    for old, layout in (("input", "list"), ("input-grid", "grid"), ("deck", "grid")):
        assert views_links(search_page(client, "bolt", view=old))[
            {"grid": "Images", "list": "List"}[layout]
        ] == (layout, True)
    assert "deck-list" in search_page(client, "bolt", view="deck-list")


def test_the_switch_and_the_slider_are_the_same_height():
    from pathlib import Path

    import magicdb

    css = (Path(magicdb.__file__).parent / "static" / "app.css").read_text(encoding="utf-8")
    slider = re.search(r"\n\.slider \{([^}]*)\}", css).group(1)
    assert "height: 2rem;" in slider


def test_on_a_phone_the_sliders_are_icons_on_one_line(client):
    page = search_page(client, "bolt")
    bar = page.split('<div class="views">')[1].split('<details class="info">')[0]
    # five controls, each a drawing with its name for screen readers
    controls = re.findall(r"<(?:a|button) [^>]*>.*?</(?:a|button)>", bar, re.DOTALL)
    assert len(controls) == 5
    for control in controls:
        assert 'class="slider-icon"' in control and 'aria-label="' in control, control
    from pathlib import Path

    import magicdb

    css = (Path(magicdb.__file__).parent / "static" / "app.css").read_text(encoding="utf-8")
    phone = css.split("@media (max-width: 40rem) {", 1)[1].split("\n}\n", 1)[0]
    assert ".views .slider-label {\n    display: none;" in phone
    assert "flex-wrap: nowrap;" in phone


def test_fast_input_as_images_lays_the_boxes_over_the_cards(client, card):
    editing(client, "input")
    page = search_page(client, f"set:{card['set']}", view="grid")
    assert 'class="sort-line"' in page and 'class="card-grid"' in page  # as a plain search
    assert "fast-list" not in page
    items = page.split('<ul class="card-grid">')[1].split("</ul>")[0].split("<li>")[1:]
    first = items[0]
    face = first.split('<div class="card-face">')[1].split("</div>\n    </div>")[0]
    assert '<div class="card-tools fast-tools">' in face  # over the image, not under it
    assert face.count('class="bigcheck') == 2 and 'class="tool-label"' in face
    assert 'hx-sync="closest li:queue all"' in face
    assert 'id="owned-' in face  # the badge a click redraws
    assert len(items) == page.count('name="finish" value="foil"')  # one card per printing


def test_a_box_on_an_image_ticks_like_one_in_the_list(client, card, fixture_cards):
    paper = next(c for c in fixture_cards if "nonfoil" in c["finishes"] and not c["digital"])
    data = {"printing": paper["id"], "finish": "nonfoil", "action": "add"}
    editing(client, "input")
    answer = client.post("/fast/toggle", data=data, headers=HTMX)
    assert 'aria-checked="true"' in answer.text
    page = search_page(client, f'!"{paper["name"]}" set:{paper["set"]}', view="grid")
    assert 'class="bigcheck is-on"' in page


def test_deck_mode_as_a_list_puts_the_buttons_where_the_boxes_are(client, card):
    deck = make_deck(client, "Humans")
    click(client, deck, card)
    page = search_page(client, f'!"{card["name"]}" unique:prints', view="list")
    assert 'class="card-list fast-list deck-list"' in page and "card-grid" not in page
    row = page.split("<tbody>")[1].split("</tbody>")[0]
    assert row.count("<tr>") == 1  # one row per card
    cells = row.split("<td")
    assert 'class="deck-count"' in cells[-1] and shown(row) == 1  # the last column
    assert ">Humans</th>" in page  # the column is headed by the deck
    assert 'class="sorter' in page.split("<thead>")[1].split("</thead>")[0]


def test_deck_mode_as_images_lays_the_buttons_over_the_card(client, card):
    make_deck(client, "Humans")
    page = search_page(client, f'!"{card["name"]}"', view="grid")
    face = page.split('<div class="card-face">')[1].split('<span class="caption">')[0]
    assert '<div class="card-tools deck-tools">' in face and 'class="deck-count"' in face
    assert f'<span class="set-code">{card["set"]}</span>' in page.split("card-grid")[1]


def test_the_controls_on_images_are_faint_until_pointed_at():
    from pathlib import Path

    import magicdb

    css = (Path(magicdb.__file__).parent / "static" / "app.css").read_text(encoding="utf-8")
    faint = re.search(r"\.card-tools form \{[^}]*opacity: ([\d.]+)", css)
    assert faint and float(faint.group(1)) < 0.5
    assert re.search(r"\.card-face:hover \.card-tools form,[^{]*\{\s*opacity: 1;", css)
    assert "@media (hover: none)" in css
    # centred on the art box, a third of the way down the card
    tools = re.search(r"\n\.card-tools \{([^}]*)\}", css).group(1)
    assert "top: 33%;" in tools and "translateY(-50%)" in tools
    # the finish's name is never wider than its box, and is cut rather than spilling out
    label = re.search(r"\n\.tool-label \{([^}]*)\}", css).group(1)
    box = re.search(r"\n\.card-tools \.bigcheck \{([^}]*)\}", css).group(1)
    width = re.search(r"width: ([\d.]+rem)", box).group(1)
    assert f"max-width: {width};" in label and "overflow: hidden;" in label
    assert "text-overflow: ellipsis;" in label
    # a card already in the deck: its number on the accent colour, with the accent's text
    # colour, not the page's (which is black on light pages)
    in_deck = re.search(r"\n\n\.card-tools \.deck-n\.is-in \{([^}]*)\}", css).group(1)
    assert "background: var(--accent);" in in_deck and "color: var(--accent-text);" in in_deck
