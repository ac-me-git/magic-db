"""Statistics of a deck: types, colours, mana curve, mana symbols (SPEC §14)."""

from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from magicdb import db
from magicdb.app import create_app
from magicdb.decks import service

# Main deck: 4 Lightning Bolt {R}, 2 Serra Angel {3}{W}{W}, 1 Niv-Mizzet Reborn {W}{U}{B}{R}{G},
# 3 Boros Reckoner {R/W}{R/W}{R/W}, 1 Sol Ring {1}, 1 Fireball {X}{R}, 1 Dryad Arbor (a land
# that is also a green creature), 10 Mountain. Sideboard: 2 Counterspell {U}{U}.
LISTING = (
    "4 Lightning Bolt\n2 Serra Angel\n1 Niv-Mizzet Reborn\n3 Boros Reckoner\n1 Sol Ring\n"
    "1 Fireball\n1 Dryad Arbor\n10 Mountain\nSideboard\n2 Counterspell\n"
)


@pytest.fixture
def conn(catalog):
    with closing(db.connect(catalog)) as connection:
        yield connection


@pytest.fixture
def deck(conn):
    deck_id = service.create_deck(conn, "Test")
    assert service.import_text(conn, deck_id, LISTING).unknown == []
    return deck_id


def stats_of(conn, deck_id):
    return service.deck_stats(service.deck_rows(conn, deck_id))


def as_dict(bars):
    return {bar.key: (bar.copies, bar.cards) for bar in bars}


def test_totals(conn, deck):
    stats = stats_of(conn, deck)
    assert (stats.copies, stats.cards, stats.sideboard) == (23, 8, 2)
    assert (stats.lands, stats.spells) == (11, 12)
    # spells: 4×1 + 2×5 + 1×5 + 3×3 + 1×1 + 1×1 = 30 over 12 cards
    assert stats.average_mv == pytest.approx(30 / 12)


def test_by_type_counts_each_type_of_a_card(conn, deck):
    types = as_dict(stats_of(conn, deck).charts["type"])
    assert types == {
        "Creature": (7, 4),  # Angel, Niv-Mizzet, Reckoner, and the Dryad Arbor
        "Instant": (4, 1),
        "Sorcery": (1, 1),
        "Artifact": (1, 1),
        "Land": (11, 2),  # Mountains and the Dryad Arbor
    }
    order = [bar.key for bar in stats_of(conn, deck).charts["type"]]
    assert order == ["Creature", "Instant", "Sorcery", "Artifact", "Land"]


def test_colours_leave_lands_out(conn, deck):
    colours = as_dict(stats_of(conn, deck).charts["color"])
    assert colours == {
        "w": (2, 1),  # Serra Angel
        "r": (5, 2),  # Lightning Bolt, Fireball
        "m": (4, 2),  # Niv-Mizzet Reborn, Boros Reckoner
        "c": (1, 1),  # Sol Ring — not the Mountains, and not the green Dryad Arbor
    }
    assert [bar.key for bar in stats_of(conn, deck).charts["color"]] == ["w", "r", "m", "c"]


def test_mana_curve_leaves_lands_out(conn, deck):
    curve = as_dict(stats_of(conn, deck).charts["mana_value"])
    assert curve == {"1": (6, 3), "3": (3, 1), "5": (3, 2)}
    assert sum(copies for copies, _ in curve.values()) == 12


def test_mana_symbols_in_costs(conn, deck):
    pips = as_dict(stats_of(conn, deck).charts["pips"])
    assert pips == {
        "w": (2 * 2 + 1 + 3 * 3, 3),  # Angel {W}{W}, Niv-Mizzet {W}, Reckoner three hybrid
        "u": (1, 1),
        "b": (1, 1),
        "r": (4 + 1 + 3 * 3 + 1, 4),  # Bolt, Niv-Mizzet, Reckoner, Fireball
        "g": (1, 1),
    }
    # generic and X mana are not colours; the sideboard's blue is not counted
    assert pips["u"] == (1, 1)


def test_sideboard_is_not_analysed(conn, deck):
    with_side = stats_of(conn, deck)
    side = next(r for r in service.deck_rows(conn, deck) if r.board == "side")
    service.remove_card(conn, deck, side.id)
    without = stats_of(conn, deck)
    assert without.sideboard == 0
    assert (without.copies, without.charts) == (with_side.copies, with_side.charts)


def test_empty_deck_and_sideboard_only(conn):
    empty = service.create_deck(conn, "Empty")
    stats = stats_of(conn, empty)
    assert (stats.copies, stats.lands, stats.spells, stats.average_mv) == (0, 0, 0, None)
    assert all(bars == [] for bars in stats.charts.values())
    service.add_card(conn, empty, name="Counterspell", quantity=2, board="side")
    assert (stats_of(conn, empty).copies, stats_of(conn, empty).sideboard) == (0, 2)


def test_lands_only(conn):
    lands = service.create_deck(conn, "Lands")
    service.add_card(conn, lands, name="Mountain", quantity=20)
    stats = stats_of(conn, lands)
    assert (stats.lands, stats.spells, stats.average_mv) == (20, 0, None)
    assert stats.charts["color"] == [] and stats.charts["mana_value"] == []


def test_cards_unknown_to_the_catalog_do_not_break_it(settings):
    db.migrate(settings)
    with closing(db.connect(settings)) as conn:
        deck = service.create_deck(conn, "Old")
        conn.execute(
            "INSERT INTO deck_cards (deck_id, oracle_id, name, quantity) VALUES (?, 'o', 'Mystery', 4)",
            (deck,),
        )
        stats = stats_of(conn, deck)
        assert (stats.copies, stats.spells, stats.average_mv) == (4, 4, None)
        assert all(bars == [] for bars in stats.charts.values())


# --- the page -------------------------------------------------------------------------


@pytest.fixture
def client(catalog, deck):
    return TestClient(create_app(catalog), follow_redirects=False)


def test_deck_has_two_tabs(client):
    listing = client.get("/decks/1").text
    assert '<a href="/decks/1" aria-current="page">List</a>' in listing
    assert '<a href="/decks/1/stats">Statistics</a>' in listing
    stats = client.get("/decks/1/stats").text
    assert '<a href="/decks/1">List</a>' in stats
    assert '<a href="/decks/1/stats" aria-current="page">Statistics</a>' in stats


def test_deck_statistics_page(client):
    page = client.get("/decks/1/stats")
    assert page.status_code == 200
    body = page.text
    for expected in [
        "<title>Test · Statistics",
        "<dd>23</dd>",
        "<dd>11</dd>",
        "<dd>12</dd>",
        "<dd>2.50</dd>",
        "These numbers are about the main deck.",
        "The sideboard (2 cards) is not counted.",
        "By card type",
        "By mana value (lands excluded)",
        "By colour (lands excluded)",
        "Mana symbols in the costs",
        ">Multicolour<",
        ">Creature<",
        'class="bar color-r" style="width: ',
        'class="dial',
    ]:
        assert expected in body, expected
    assert body.count('<section class="chart">') == 4


def test_deck_statistics_page_when_empty_missing_or_locked(client):
    client.post("/decks", data={"name": "Empty"})
    empty = client.get("/decks/2/stats")
    assert empty.status_code == 200 and "there is nothing to count yet" in empty.text
    assert '<section class="chart">' not in empty.text
    for missing in ("99", "abc"):
        assert client.get(f"/decks/{missing}/stats").status_code == 404
    client.post("/lock", data={"state": "on"})
    assert client.get("/decks/1/stats").status_code == 200  # reading is always allowed


def test_deck_statistics_in_italian(client):
    client.get("/lang/it")
    body = client.get("/decks/1/stats").text
    for expected in [
        "Statistiche",
        "Terre",
        "Valore di mana medio",
        "<dd>2,50</dd>",
        "Simboli di mana nei costi",
        "La sideboard (2 carte) non è contata.",
    ]:
        assert expected in body, expected


def test_collection_statistics_still_use_the_shared_charts(client, fixture_cards):
    bolt = next(c["id"] for c in fixture_cards if c["name"] == "Lightning Bolt")
    client.post("/add", data={"printing": bolt, "quantity": "3"})
    body = client.get("/stats").text
    assert "By colour" in body and 'class="bar color-r" style="width: 100.0%"' in body
    assert body.count('<section class="chart">') >= 8
