"""Decks: lists of cards compared with the collection (SPEC §14)."""

import re
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magicdb import db, i18n, search
from magicdb.app import create_app
from magicdb.collection import exporters, importers
from magicdb.collection import service as collection
from magicdb.collection.service import CollectionError
from magicdb.decks import service, text

# --- reading a deck list --------------------------------------------------------------


def parsed(listing):
    return [(line.quantity, line.name, line.board) for line in text.parse(listing)]


def test_plain_list():
    assert parsed("4 Lightning Bolt\n2 Counterspell\n1 Sol Ring") == [
        (4, "Lightning Bolt", "main"),
        (2, "Counterspell", "main"),
        (1, "Sol Ring", "main"),
    ]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("4 Lightning Bolt", (4, "Lightning Bolt")),
        ("4x Lightning Bolt", (4, "Lightning Bolt")),
        ("4X Lightning Bolt", (4, "Lightning Bolt")),
        ("4 x Lightning Bolt", (4, "Lightning Bolt")),
        ("  12   Mountain  ", (12, "Mountain")),
        ("Lightning Bolt", (1, "Lightning Bolt")),
        ("1 Sol Ring (C21) 263", (1, "Sol Ring")),
        ("1 Sol Ring (C21)", (1, "Sol Ring")),
        ("1 Sol Ring [C21]", (1, "Sol Ring")),
        ("1 Sol Ring (C21) 263 *F*", (1, "Sol Ring")),
        ("1 Sol Ring *F*", (1, "Sol Ring")),
        ("1 Sol Ring (PLST) CLB-187", (1, "Sol Ring")),
        ("1 Sol Ring (C21) 263 #ramp #staple", (1, "Sol Ring")),
        ("1 Fire // Ice", (1, "Fire // Ice")),
        ("1 Fire / Ice", (1, "Fire // Ice")),
        ("1 Fire/Ice", (1, "Fire // Ice")),
        ("2 Jace, the Mind Sculptor", (2, "Jace, the Mind Sculptor")),
        ("1 Urza's Saga", (1, "Urza's Saga")),
        ("3 Borrowing 100,000 Arrows", (3, "Borrowing 100,000 Arrows")),  # a number in a name
    ],
)
def test_line_forms(line, expected):
    assert parsed(line) == [(*expected, "main")]


def test_sideboard_section_and_prefix():
    listing = "4 Lightning Bolt\n\nSideboard\n2 Pyroblast\nSB: 1 Smash to Smithereens\n"
    assert parsed(listing) == [
        (4, "Lightning Bolt", "main"),
        (2, "Pyroblast", "side"),
        (1, "Smash to Smithereens", "side"),
    ]
    assert parsed("SB: 3 Pyroblast\n4 Lightning Bolt") == [
        (3, "Pyroblast", "side"),
        (4, "Lightning Bolt", "main"),  # the prefix is for its own line only
    ]
    assert parsed("sideboard:\n1 Pyroblast\nMainboard\n1 Lightning Bolt") == [
        (1, "Pyroblast", "side"),
        (1, "Lightning Bolt", "main"),
    ]


def test_arena_export():
    listing = (
        "About\nName Red burn\n\nCommander\n1 Niv-Mizzet Reborn (WAR) 208\n\n"
        "Deck\n4 Lightning Bolt (M10) 146\n20 Mountain (M21) 312\n\n"
        "Sideboard\n2 Fire // Ice (APC) 128\n\nMaybeboard\n4 Shock\n"
    )
    assert parsed(listing) == [
        (1, "Niv-Mizzet Reborn", "main"),
        (4, "Lightning Bolt", "main"),
        (20, "Mountain", "main"),
        (2, "Fire // Ice", "side"),
    ]


def test_type_groups_comments_and_blank_lines_are_not_cards():
    listing = (
        "// my deck\n# another comment\nCreatures (2)\n2 Serra Angel\n\n\n"
        "Instants (4):\n4 Lightning Bolt\nLands\n20 Mountain\n"
        "Sideboard (1)\nArtifacts\n1 Sol Ring\n"
    )
    assert parsed(listing) == [
        (2, "Serra Angel", "main"),
        (4, "Lightning Bolt", "main"),
        (20, "Mountain", "main"),
        (1, "Sol Ring", "side"),  # a type group does not leave the sideboard
    ]
    assert parsed("") == [] and parsed("\n\n// nothing\n") == []


def test_parser_remembers_where_each_line_was():
    lines = text.parse("// x\n4 Lightning Bolt (M10) 146\nNot a card\n")
    assert [(line.number, line.text, line.counted) for line in lines] == [
        (2, "4 Lightning Bolt (M10) 146", True),
        (3, "Not a card", False),
    ]


# --- the service ----------------------------------------------------------------------


@pytest.fixture
def conn(catalog):
    with closing(db.connect(catalog)) as connection:
        yield connection


def pid(conn, name, set_code):
    return conn.execute(
        "SELECT id FROM cat.printings WHERE name = ? AND set_code = ?", (name, set_code)
    ).fetchone()[0]


def own(conn, name, set_code, copies, **kwargs):
    collection.add_entry(conn, pid(conn, name, set_code), copies, **kwargs)


def code(excinfo):
    return excinfo.value.code


def test_create_rename_delete(conn):
    deck = service.create_deck(conn, "  Red   burn ", "Modern", "fast")
    row = service.get_deck(conn, deck)
    assert (row["name"], row["format"], row["notes"]) == ("Red burn", "Modern", "fast")
    service.update_deck(conn, deck, 'Burn "v2"', "", "")
    row = service.get_deck(conn, deck)
    assert (row["name"], row["format"], row["notes"]) == ("Burn 'v2'", None, None)
    service.add_card(conn, deck, name="Lightning Bolt", quantity=4)
    service.delete_deck(conn, deck)
    assert service.list_decks(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM deck_cards").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("", "bad_deck_name"),
        ("   ", "bad_deck_name"),
        ("x" * 81, "bad_deck_name"),
        ("BURN", "deck_exists"),
    ],
)
def test_deck_names(conn, name, expected):
    service.create_deck(conn, "Burn")
    with pytest.raises(CollectionError) as excinfo:
        service.create_deck(conn, name)
    assert code(excinfo) == expected
    assert len(service.list_decks(conn)) == 1


def test_add_card_by_name_face_or_printing(conn):
    deck = service.create_deck(conn, "Test")
    assert service.add_card(conn, deck, name="lightning bolt", quantity=3) == "Lightning Bolt"
    assert service.add_card(conn, deck, name="Delver of Secrets") == (
        "Delver of Secrets // Insectile Aberration"
    )
    assert (
        service.add_card(conn, deck, printing=pid(conn, "Sol Ring", "c21"), board="side")
        == "Sol Ring"
    )
    assert (
        service.add_card(conn, deck, printing=pid(conn, "Lightning Bolt", "lea"))
        == "Lightning Bolt"
    )
    rows = {(row.name, row.board): row.quantity for row in service.deck_rows(conn, deck)}
    assert rows == {
        ("Lightning Bolt", "main"): 4,  # the Alpha printing is the same card: one row
        ("Delver of Secrets // Insectile Aberration", "main"): 1,
        ("Sol Ring", "side"): 1,
    }


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"name": "Lightnin"}, "unknown_deck_card"),
        ({"name": ""}, "unknown_deck_card"),
        ({"printing": "00000000-0000-4000-8000-000000000000"}, "unknown_deck_card"),
        ({"name": "Lightning Bolt", "quantity": 0}, "bad_deck_quantity"),
        ({"name": "Lightning Bolt", "quantity": "many"}, "bad_deck_quantity"),
        ({"name": "Lightning Bolt", "quantity": 1000}, "bad_deck_quantity"),
        ({"name": "Lightning Bolt", "board": "maybe"}, "bad_board"),
    ],
)
def test_add_card_rejects(conn, kwargs, expected):
    deck = service.create_deck(conn, "Test")
    with pytest.raises(CollectionError) as excinfo:
        service.add_card(conn, deck, **kwargs)
    assert code(excinfo) == expected
    assert service.deck_rows(conn, deck) == []
    with pytest.raises(CollectionError) as excinfo:
        service.add_card(conn, 99, name="Lightning Bolt")
    assert code(excinfo) == "unknown_deck"


def test_tokens_are_not_deck_cards(conn):
    token = conn.execute("SELECT name FROM cat.printings WHERE layout = 'token'").fetchone()[0]
    assert service.find_card(conn, token) is None


def test_update_and_remove_card(conn):
    deck = service.create_deck(conn, "Test")
    service.add_card(conn, deck, name="Lightning Bolt", quantity=4)
    service.add_card(conn, deck, name="Lightning Bolt", quantity=2, board="side")
    main, side = sorted(service.deck_rows(conn, deck), key=lambda r: r.board)
    service.update_card(conn, deck, main.id, 3, "main")
    assert [r.quantity for r in service.deck_rows(conn, deck)] == [3, 2]
    service.update_card(conn, deck, side.id, 1, "main")  # onto the board where it already is
    assert [(r.quantity, r.board) for r in service.deck_rows(conn, deck)] == [(4, "main")]
    (row,) = service.deck_rows(conn, deck)
    for bad, expected in [((0, "main"), "bad_deck_quantity"), ((1, "x"), "bad_board")]:
        with pytest.raises(CollectionError) as excinfo:
            service.update_card(conn, deck, row.id, *bad)
        assert code(excinfo) == expected
    other = service.create_deck(conn, "Other")
    with pytest.raises(CollectionError) as excinfo:
        service.remove_card(conn, other, row.id)  # a row of another deck
    assert code(excinfo) == "unknown_deck_row"
    service.remove_card(conn, deck, row.id)
    assert service.deck_rows(conn, deck) == []


def test_decks_never_touch_the_collection(conn):
    own(conn, "Lightning Bolt", "m10", 2)
    before = [tuple(r) for r in conn.execute("SELECT * FROM entries")]
    history = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    deck = service.create_deck(conn, "Test")
    service.add_card(conn, deck, name="Lightning Bolt", quantity=4)
    service.import_text(conn, deck, "4 Sol Ring\n1 Serra Angel")
    service.delete_deck(conn, deck)
    assert [tuple(r) for r in conn.execute("SELECT * FROM entries")] == before
    assert conn.execute("SELECT COUNT(*) FROM history").fetchone()[0] == history


# --- importing a list -----------------------------------------------------------------


def test_import_text(conn):
    deck = service.create_deck(conn, "Test")
    report = service.import_text(
        conn,
        deck,
        "4 Lightning Bolt (M10) 146\n2x serra angel\n1 Fire / Ice\n1 Delver of Secrets\n"
        "Sideboard\n3 Sol Ring\n2 Lightning Bolt\n",
    )
    assert (report.cards, report.copies, report.unknown) == (6, 13, [])
    rows = [(r.board, r.name, r.quantity) for r in service.deck_rows(conn, deck)]
    assert sorted(rows) == [
        ("main", "Delver of Secrets // Insectile Aberration", 1),
        ("main", "Fire // Ice", 1),
        ("main", "Lightning Bolt", 4),
        ("main", "Serra Angel", 2),
        ("side", "Lightning Bolt", 2),
        ("side", "Sol Ring", 3),
    ]
    again = service.import_text(conn, deck, "1 Lightning Bolt")  # adds to what is there
    assert again.copies == 1
    assert sum(r.quantity for r in service.deck_rows(conn, deck) if r.name == "Lightning Bolt") == 7


def test_import_reports_what_is_not_a_card(conn):
    deck = service.create_deck(conn, "Test")
    report = service.import_text(
        conn, deck, "4 Lightning Bolt\n2 Lightnig Bolt\n1 Qqqqzzzz Xxxx\nSome heading\n3 Sol Ring"
    )
    assert (report.cards, report.copies) == (2, 7)
    assert [(u.number, u.text) for u in report.unknown] == [
        (2, "2 Lightnig Bolt"),
        (3, "1 Qqqqzzzz Xxxx"),
        (4, "Some heading"),
    ]
    assert report.unknown[0].suggestions[0] == "Lightning Bolt"
    assert report.unknown[1].suggestions == []
    assert sorted(r.name for r in service.deck_rows(conn, deck)) == ["Lightning Bolt", "Sol Ring"]


def test_import_of_nothing(conn):
    deck = service.create_deck(conn, "Test")
    report = service.import_text(conn, deck, "\n// just a comment\nCreatures (0)\n")
    assert (report.cards, report.copies, report.unknown) == (0, 0, [])
    with pytest.raises(CollectionError) as excinfo:
        service.import_text(conn, 99, "1 Sol Ring")
    assert code(excinfo) == "unknown_deck"


def test_export_text_round_trip(conn):
    deck = service.create_deck(conn, "Test")
    service.import_text(conn, deck, "4 Lightning Bolt\n1 Fire // Ice\nSideboard\n2 Sol Ring")
    exported = service.export_text(conn, deck)
    assert exported == "Deck\n1 Fire // Ice\n4 Lightning Bolt\n\nSideboard\n2 Sol Ring\n"
    copy = service.create_deck(conn, "Copy")
    assert service.import_text(conn, copy, exported).unknown == []
    assert service.export_text(conn, copy) == exported
    assert service.export_text(conn, service.create_deck(conn, "Empty")) == "\n"


# --- completion and contested cards ---------------------------------------------------


@pytest.fixture
def decks(conn):
    """Two decks that both want Lightning Bolt, against a small collection.

    Owned: 6 Lightning Bolt (4 M10 + 2 Alpha, one of them foil), 1 Serra Angel, 5 Sol Ring.
    Burn wants 4 Bolt, 4 Serra Angel, 1 Sol Ring. Control wants 4 Bolt (3 main + 1 side),
    2 Sol Ring, 2 Fire // Ice.
    """
    own(conn, "Lightning Bolt", "m10", 3)
    own(conn, "Lightning Bolt", "m10", 1, finish="foil")
    own(conn, "Lightning Bolt", "lea", 2, condition="PL")
    own(conn, "Serra Angel", "dom", 1)
    own(conn, "Sol Ring", "c21", 5)
    burn = service.create_deck(conn, "Burn", "Modern")
    service.import_text(conn, burn, "4 Lightning Bolt\n4 Serra Angel\n1 Sol Ring")
    control = service.create_deck(conn, "Control")
    service.import_text(
        conn, control, "3 Lightning Bolt\n2 Sol Ring\n2 Fire // Ice\nSideboard\n1 Lightning Bolt"
    )
    return conn, burn, control


def by_name(rows):
    return {(row.name, row.board): row for row in rows}


def test_completion_counts_every_printing_owned(decks):
    conn, burn, control = decks
    summaries = {deck.name: deck for deck in service.list_decks(conn)}
    burn_summary = summaries["Burn"]
    # 4 of 4 Bolts, 1 of 4 Angels, 1 of 1 Ring -> 6 of 9
    assert (burn_summary.copies, burn_summary.cards, burn_summary.have) == (9, 3, 6)
    assert (burn_summary.missing, burn_summary.percent, burn_summary.complete) == (3, 66, False)
    control_summary = summaries["Control"]
    # 4 of 4 Bolts, 2 of 2 Rings, 0 of 2 Fire // Ice -> 6 of 8
    assert (control_summary.copies, control_summary.have, control_summary.percent) == (8, 6, 75)
    assert service.summary(conn, burn).percent == 66


def test_row_states(decks):
    conn, burn, control = decks
    rows = by_name(service.deck_rows(conn, burn))
    bolt = rows[("Lightning Bolt", "main")]
    # 6 owned is enough for Burn's 4, but Burn and Control together want 8
    assert (bolt.owned, bolt.have, bolt.missing, bolt.wanted, bolt.contested) == (6, 4, 0, 8, True)
    assert bolt.others == [(control, "Control", 4)]
    angel = rows[("Serra Angel", "main")]
    assert (angel.owned, angel.have, angel.missing, angel.contested, angel.others) == (
        1,
        1,
        3,
        False,
        [],
    )
    ring = rows[("Sol Ring", "main")]
    # in both decks, and 5 owned cover the 3 wanted: not contested
    assert (ring.owned, ring.wanted, ring.missing, ring.contested) == (5, 3, 0, False)
    assert ring.others == [(control, "Control", 2)]


def test_a_card_in_both_boards_counts_the_main_board_first(decks):
    conn, burn, control = decks
    collection.remove_entry(conn, 3)  # the two Alpha Bolts: 4 left
    collection.remove_entry(conn, 2)  # the foil: 3 left
    rows = by_name(service.deck_rows(conn, control))
    main, side = rows[("Lightning Bolt", "main")], rows[("Lightning Bolt", "side")]
    assert (main.quantity, main.have, main.missing) == (3, 3, 0)
    assert (side.quantity, side.have, side.missing) == (1, 0, 1)
    assert main.owned == side.owned == 3
    assert service.summary(conn, control).have == 3 + 2  # Bolts + Rings


def test_summary_contested_count_and_shortages(decks):
    conn, burn, control = decks
    summaries = {deck.name: deck for deck in service.list_decks(conn)}
    assert (summaries["Burn"].contested, summaries["Control"].contested) == (1, 1)
    (short,) = service.shortages(conn)
    assert (short.name, short.owned, short.wanted) == ("Lightning Bolt", 6, 8)
    assert short.decks == [(burn, "Burn", 4), (control, "Control", 4)]

    own(conn, "Lightning Bolt", "a25", 2)  # now 8: enough for both
    assert service.shortages(conn) == []
    assert all(deck.contested == 0 for deck in service.list_decks(conn))
    assert not by_name(service.deck_rows(conn, burn))[("Lightning Bolt", "main")].contested


def test_a_card_missing_in_a_deck_is_missing_not_contested(decks):
    conn, burn, control = decks
    third = service.create_deck(conn, "Third")
    service.add_card(conn, third, name="Serra Angel", quantity=1)
    rows = by_name(service.deck_rows(conn, burn))
    angel = rows[("Serra Angel", "main")]
    assert angel.missing == 3 and not angel.contested  # "missing" says more
    alone = by_name(service.deck_rows(conn, third))[("Serra Angel", "main")]
    assert alone.missing == 0 and alone.contested  # 1 owned, 5 wanted in all
    names = [short.name for short in service.shortages(conn)]
    assert names == ["Serra Angel", "Lightning Bolt"]  # the biggest gap first


def test_complete_deck_and_empty_deck(conn):
    own(conn, "Sol Ring", "c21", 1)
    full = service.create_deck(conn, "Full")
    service.add_card(conn, full, name="Sol Ring")
    empty = service.create_deck(conn, "Empty")
    summaries = {deck.name: deck for deck in service.list_decks(conn)}
    assert (summaries["Full"].percent, summaries["Full"].complete) == (100, True)
    assert (summaries["Empty"].copies, summaries["Empty"].percent, summaries["Empty"].complete) == (
        0,
        0,
        False,
    )
    assert service.deck_rows(conn, empty) == []


def test_percent_never_rounds_up(conn):
    own(conn, "Mountain", "m21", 199)
    deck = service.create_deck(conn, "Lands")
    service.add_card(conn, deck, name="Mountain", quantity=200)
    assert service.summary(conn, deck).percent == 99


def test_rows_carry_catalog_facts_and_are_grouped(decks, fixture_cards):
    conn, burn, control = decks
    rows = service.deck_rows(conn, control)
    assert [(r.board, r.group) for r in rows] == [
        ("main", "Instant"),
        ("main", "Instant"),
        ("main", "Artifact"),
        ("side", "Instant"),
    ]
    fire = by_name(rows)[("Fire // Ice", "main")]
    assert fire.mana_cost == "{1}{R} // {1}{U}" and fire.printing and fire.cmc == 4
    bolt = by_name(rows)[("Lightning Bolt", "main")]
    prices = [
        float(c["prices"]["eur"])
        for c in fixture_cards
        if c["name"] == "Lightning Bolt" and c["prices"].get("eur") and not c["digital"]
    ]
    assert bolt.price == min(prices)  # the cheapest printing, to estimate what is missing
    dollars = by_name(service.deck_rows(conn, control, "usd"))[("Lightning Bolt", "main")]
    assert dollars.price != bolt.price


def test_decks_with_card(decks):
    conn, burn, control = decks
    oracle = conn.execute(
        "SELECT oracle_id FROM cat.printings WHERE name = 'Lightning Bolt'"
    ).fetchone()[0]
    assert [tuple(r) for r in service.decks_with_card(conn, oracle)] == [
        (burn, "Burn", 4),
        (control, "Control", 4),  # main and sideboard together
    ]
    assert service.decks_with_card(conn, "nothing") == []


def test_reading_decks_without_a_catalog(settings):
    db.migrate(settings)
    with closing(db.connect(settings)) as conn:
        deck = service.create_deck(conn, "Old")
        conn.execute(
            "INSERT INTO deck_cards (deck_id, oracle_id, name, quantity) VALUES (?, 'o', 'Lightning Bolt', 4)",
            (deck,),
        )
        (row,) = service.deck_rows(conn, deck)
        assert (row.name, row.quantity, row.missing, row.printing, row.group) == (
            "Lightning Bolt",
            4,
            4,
            None,
            "Other",
        )
        assert service.list_decks(conn)[0].percent == 0
        with pytest.raises(CollectionError) as excinfo:
            service.import_text(conn, deck, "1 Sol Ring")
        assert code(excinfo) == "no_catalog"


# --- search and export ----------------------------------------------------------------


def test_deck_search_filter(decks):
    conn, burn, control = decks
    names = {row["name"] for row in search.run(conn, 'deck:"burn"').rows}
    assert names == {"Lightning Bolt", "Serra Angel", "Sol Ring"}
    assert {row["name"] for row in search.run(conn, "deck:control -t:artifact").rows} == {
        "Lightning Bolt",
        "Fire // Ice",
    }
    assert search.run(conn, "deck:nothing").total == 0
    assert search.run(conn, "deck:burn have:no").total == 0  # all of Burn's cards are owned
    assert search.run(conn, "-deck:burn t:angel").total > 0


def test_json_export_and_import_carry_the_decks(decks, catalog, tmp_path):
    conn, burn, control = decks
    dump = exporters.export_json(conn)
    assert '"decks"' in dump
    parsed_dump = importers.parse_file(dump.encode())
    assert [deck["name"] for deck in parsed_dump.decks] == ["Burn", "Control"]
    assert parsed_dump.decks[0]["format"] == "Modern"

    service.delete_deck(conn, burn)
    assert service.restore(conn, parsed_dump.decks) == ["Burn"]  # Control is already there
    restored = next(deck for deck in service.list_decks(conn) if deck.name == "Burn")
    assert (restored.copies, restored.format) == (9, "Modern")
    assert (
        service.export_text(conn, restored.id)
        == "Deck\n4 Lightning Bolt\n4 Serra Angel\n1 Sol Ring\n"
    )
    assert service.restore(
        conn, [{"name": ""}, {"cards": []}, {"name": "X", "cards": [{"quantity": "4"}]}]
    ) == ["X"]


# --- the pages ------------------------------------------------------------------------


@pytest.fixture
def client(catalog):
    return TestClient(create_app(catalog), follow_redirects=False)


@pytest.fixture
def ids(fixture_cards):
    return lambda name, set_code: next(
        c["id"] for c in fixture_cards if c["name"] == name and c["set"] == set_code
    )


@pytest.fixture
def site(client, ids):
    """The same two decks as above, made through the pages."""
    for name, set_code, copies in [
        ("Lightning Bolt", "m10", 4),
        ("Lightning Bolt", "lea", 2),
        ("Serra Angel", "dom", 1),
        ("Sol Ring", "c21", 5),
    ]:
        client.post("/add", data={"printing": ids(name, set_code), "quantity": str(copies)})
    assert (
        client.post("/decks", data={"name": "Burn", "format": "Modern"}).headers["location"]
        == "/decks/1"
    )
    client.post("/decks/1/import", data={"text": "4 Lightning Bolt\n4 Serra Angel\n1 Sol Ring"})
    client.post("/decks", data={"name": "Control"})
    client.post(
        "/decks/2/import",
        data={"text": "3 Lightning Bolt\n2 Sol Ring\n2 Fire // Ice\nSideboard\n1 Lightning Bolt"},
    )
    return client


def test_decks_page_when_empty(client):
    page = client.get("/decks")
    assert page.status_code == 200 and "No decks yet." in page.text
    assert 'action="/decks"' in page.text
    assert 'href="/decks"' in client.get("/").text  # in the header of every page


def test_decks_page_lists_completion_and_shortages(site):
    page = site.get("/decks").text
    assert '<a class="deck-name" href="/decks/1">Burn</a>' in page and ">Modern</span>" in page
    assert (
        'style="--p: 66"' in page and 'style="--p: 75"' in page and "66%" in page and "75%" in page
    )
    assert "Cards that several decks want and you do not have enough of (1)" in page
    shortage = page.split("several decks want")[1]
    assert "Lightning Bolt" in shortage and ">6</td>" in shortage and ">8</td>" in shortage
    assert (
        '<a href="/decks/1">Burn</a> ×4' in shortage
        and '<a href="/decks/2">Control</a> ×4' in shortage
    )


def test_deck_page(site):
    page = site.get("/decks/1")
    assert page.status_code == 200
    body = page.text
    for expected in [
        "<title>Burn",
        "Main deck (9)",
        "66%",
        "3 missing",
        "contested: 6 for 8 wanted",
        "have it",
        "also in",
        '<a href="/decks/2">Control</a> ×4',
        "To buy the missing",
        'href="/decks/1/export.txt"',
        "deck%3A%22Burn%22",
        ">Instant</th>",
        ">Creature</th>",
        'alt="{R}"',
    ]:
        assert expected in body, expected
    assert "Sideboard (" not in body  # Burn has none
    assert "Sideboard (1)" in site.get("/decks/2").text
    assert 'class="is-missing"' in body and 'class="is-contested"' in body


@pytest.mark.parametrize("deck", ["99", "abc", "-1", "1e3"])
def test_unknown_deck_is_not_found(site, deck):
    assert site.get(f"/decks/{deck}").status_code == 404
    assert site.get(f"/decks/{deck}/export.txt").status_code == 404
    assert site.post(f"/decks/{deck}/delete").status_code == 404


def test_add_a_card_by_name(site):
    added = site.post(
        "/decks/1/cards", data={"name": "shivan dragon", "quantity": "2", "board": "side"}
    )
    assert added.status_code == 303 and added.headers["location"] == "/decks/1?added=Shivan+Dragon"
    page = site.get(added.headers["location"]).text
    assert "Added Shivan Dragon." in page and "Sideboard (2)" in page

    wrong = site.post("/decks/1/cards", data={"name": "lightnin", "quantity": "1"})
    assert wrong.status_code == 400
    assert "No card is called exactly &#34;lightnin&#34;." in wrong.text
    assert "Did you mean" in wrong.text and "Lightning Bolt" in wrong.text
    assert 'name="name" value="lightnin"' in wrong.text  # still in the field, to correct
    assert (
        site.post("/decks/1/cards", data={"name": "Sol Ring", "quantity": "0"}).status_code == 400
    )


def test_add_a_card_from_its_page(site, ids):
    dragon = ids("Shivan Dragon", "m20")
    page = site.get(f"/card/{dragon}").text
    assert "In my decks" in page and "This card is in none of your decks." in page
    assert 'action="/deck-cards"' in page and '<option value="2">Control</option>' in page

    response = site.post(
        "/deck-cards",
        data={"deck": "2", "printing": dragon, "quantity": "3", "next": f"/card/{dragon}#decks"},
    )
    assert (response.status_code, response.headers["location"]) == (303, f"/card/{dragon}#decks")
    page = site.get(f"/card/{dragon}").text
    assert '<a href="/decks/2">Control</a> ×3' in page
    bolt = site.get(f"/card/{ids('Lightning Bolt', 'lea')}").text
    assert '<a href="/decks/1">Burn</a> ×4' in bolt and '<a href="/decks/2">Control</a> ×4' in bolt
    assert site.post("/deck-cards", data={"deck": "99", "printing": dragon}).status_code == 404


def test_card_page_without_decks_offers_nothing_to_add_to(client, ids):
    page = client.get(f"/card/{ids('Sol Ring', 'c21')}").text
    assert "In my decks" in page and 'action="/deck-cards"' not in page


def test_import_a_list_through_the_page(site):
    page = site.post(
        "/decks/1/import", data={"text": "2 Fire / Ice\n1 Lightnig Bolt\nNot a card\n"}
    )
    assert page.status_code == 200
    assert "2 cards added, from 1 lines." in page.text
    assert "2 lines are not cards and were left out." in page.text
    assert "Line 2: <code>1 Lightnig Bolt</code>" in page.text and "Lightning Bolt" in page.text
    # the lines to correct are back in the box, and the box is open
    assert '<details class="panel fold" open>' in page.text
    box = page.text.split("<textarea")[1].split("</textarea>")[0]
    assert "1 Lightnig Bolt\nNot a card" in box and "Fire" not in box
    assert "Fire // Ice" in site.get("/decks/1").text


def test_edit_and_remove_rows(site, catalog):
    with closing(db.connect(catalog)) as conn:
        row = conn.execute(
            "SELECT id FROM deck_cards WHERE deck_id = 1 AND name = 'Serra Angel'"
        ).fetchone()[0]
    assert (
        site.post(f"/decks/1/cards/{row}", data={"quantity": "1", "board": "side"}).status_code
        == 303
    )
    page = site.get("/decks/1").text
    assert "Sideboard (1)" in page and "Main deck (5)" in page and "100%" in page
    assert site.post(f"/decks/1/cards/{row}/remove").status_code == 303
    assert "Serra Angel" not in site.get("/decks/1").text
    assert site.post(f"/decks/1/cards/{row}/remove").status_code == 400  # already gone
    assert site.post(f"/decks/1/cards/{row}", data={"quantity": "x"}).status_code == 400
    assert site.post(f"/decks/2/cards/{row}", data={"quantity": "1"}).status_code == 400


def test_rename_and_delete_a_deck(site):
    assert (
        site.post("/decks/1", data={"name": "Burn 2", "format": "Legacy"}).headers["location"]
        == "/decks/1"
    )
    assert "<title>Burn 2" in site.get("/decks/1").text
    clash = site.post("/decks/1", data={"name": "control"})
    assert clash.status_code == 400 and "There is already a deck called control." in clash.text
    assert site.post("/decks", data={"name": "Control"}).status_code == 400
    assert site.post("/decks/1/delete").headers["location"] == "/decks"
    listing = site.get("/decks").text
    assert "Burn 2" not in listing and "several decks want" not in listing  # no more contest
    assert "Cards: 12" in site.get("/collection").text  # the collection is as it was


def test_export_a_deck_as_text(site):
    response = site.get("/decks/2/export.txt")
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert 'filename="Control.txt"' in response.headers["content-disposition"]
    assert response.text == (
        "Deck\n2 Fire // Ice\n3 Lightning Bolt\n2 Sol Ring\n\nSideboard\n1 Lightning Bolt\n"
    )


def test_decks_are_read_only_while_locked(site, catalog):
    site.post("/lock", data={"state": "on"})
    for path, data in [
        ("/decks", {"name": "New"}),
        ("/decks/1", {"name": "Renamed"}),
        ("/decks/1/delete", {}),
        ("/decks/1/cards", {"name": "Sol Ring"}),
        ("/decks/1/import", {"text": "4 Sol Ring"}),
        ("/decks/1/cards/1", {"quantity": "9"}),
        ("/decks/1/cards/1/remove", {}),
        ("/deck-cards", {"deck": "1", "printing": "x"}),
    ]:
        assert site.post(path, data=data).status_code == 423, path
    page = site.get("/decks/1").text
    assert "66%" in page and "3 missing" in page  # still readable
    for control in (
        'action="/decks/1/cards"',
        'action="/decks/1/import"',
        'action="/decks/1/delete"',
        "row-edit",
    ):
        assert control not in page, control
    assert "so nothing can change them by accident" in page
    assert 'action="/decks"' not in site.get("/decks").text
    assert site.get("/decks/1/export.txt").status_code == 200
    with closing(db.connect(catalog)) as conn:
        assert [d.copies for d in service.list_decks(conn)] == [9, 8]


def test_deck_pages_in_italian(site):
    site.get("/lang/it")
    listing = site.get("/decks").text
    assert (
        "Mazzi" in listing
        and "Carte richieste da più mazzi che non bastano per tutti (1)" in listing
    )
    page = site.get("/decks/1").text
    for expected in [
        "Mazzo principale (9)",
        "3 mancanti",
        "contesa: 6 per 8 richieste",
        "anche in",
    ]:
        assert expected in page, expected


def test_every_deck_error_has_a_message():
    source = Path(service.__file__).read_text(encoding="utf-8")
    codes = set(re.findall(r'CollectionError\(\s*"(\w+)"', source))
    assert len(codes) >= 7
    for lang in ("en", "it"):
        assert {f"error.{code}" for code in codes} <= set(i18n.strings(lang))
