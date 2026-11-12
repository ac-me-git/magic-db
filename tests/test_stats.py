"""Collection statistics (SPEC §9), on a hand-built collection.

Expected numbers are worked out here from the fixture JSON, not from the catalog.
"""

import sqlite3
from collections import defaultdict
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from magicdb import db, search, stats
from magicdb.app import create_app
from magicdb.catalog import publish
from magicdb.collection import service

# (name, set, copies, finish, location)
OWNED = [
    ("Lightning Bolt", "m10", 3, "nonfoil", "Binder"),
    ("Lightning Bolt", "lea", 1, "nonfoil", "Binder"),
    ("Serra Angel", "dom", 2, "foil", "Binder"),
    ("Niv-Mizzet Reborn", "war", 1, "nonfoil", "Deck"),
    ("Sol Ring", "c21", 4, "nonfoil", "Deck"),
    ("Dryad Arbor", "fut", 1, "nonfoil", None),
    ("Delver of Secrets // Insectile Aberration", "isd", 1, "nonfoil", None),
    ("Mountain", "m21", 10, "nonfoil", "Deck"),
    ("Jace, the Mind Sculptor", "wwk", 1, "nonfoil", None),
]


@pytest.fixture
def conn(catalog):
    with closing(db.connect(catalog)) as connection:
        yield connection


@pytest.fixture
def cards(fixture_cards):
    return {(c["name"], c["set"]): c for c in fixture_cards}


@pytest.fixture
def owned(conn, cards):
    places = {
        "Binder": service.create_location(conn, "Binder", "binder"),
        "Deck": service.create_location(conn, "Deck", "deck"),
    }
    for name, set_code, copies, finish, place in OWNED:
        service.add_entry(
            conn,
            cards[(name, set_code)]["id"],
            copies,
            finish=finish,
            location_id=places.get(place),
        )
    conn.execute("UPDATE entries SET created_at = '2025-01-0' || id || 'T00:00:00+00:00'")
    conn.commit()
    return conn


def as_dict(bars):
    return {bar.key: (bar.copies, bar.cards) for bar in bars}


def expected(cards, key_of):
    """{key: (copies, distinct cards)} counted the slow, obvious way."""
    copies, distinct = defaultdict(int), defaultdict(set)
    for name, set_code, count, finish, place in OWNED:
        card = cards[(name, set_code)]
        for key in key_of(card, finish, place):
            copies[key] += count
            distinct[key].add(card["oracle_id"])
    return {key: (copies[key], len(distinct[key])) for key in copies}


def colours(card):
    if card.get("colors") is not None:
        return set(card["colors"])
    return {c for face in card["card_faces"] for c in face.get("colors") or ()}


def front_types(card):
    words = card["type_line"].split(" // ")[0].split(" — ")[0].split()
    return [w for w in words if w not in ("Basic", "Legendary", "Snow", "World")]


# --- totals and value -----------------------------------------------------------------


def test_totals(owned, cards):
    total = stats.totals(stats.collect(owned))
    assert (total.entries, total.copies) == (9, 24)
    assert total.printings == 9
    assert total.cards == 8  # the two Lightning Bolts are one card
    priced = unpriced = 0
    value = 0.0
    for name, set_code, copies, finish, _ in OWNED:
        prices = cards[(name, set_code)]["prices"]
        price = (
            prices.get("eur")
            if finish == "nonfoil"
            else prices.get("eur_foil") or prices.get("eur")
        )
        if price is None:
            unpriced += copies
        else:
            priced += copies
            value += copies * float(price)
    assert total.unpriced == unpriced and priced + unpriced == 24
    assert total.value == pytest.approx(value) and value > 0


def test_empty_collection(conn):
    rows = stats.collect(conn)
    assert stats.totals(rows) == stats.Totals(0, 0, 0, 0, 0, 0)
    assert all(bars == [] for bars in stats.breakdowns(rows).values())
    assert stats.set_completion(conn) == [] and stats.most_valuable(rows) == []


def reprice(settings, set_code, name, **prices):
    """Give a printing exact prices in this test's own copy of the catalog."""
    assignments = ", ".join(f"price_{key} = ?" for key in prices)
    with closing(sqlite3.connect(publish.live_catalog(settings))) as catalog:
        catalog.execute(
            f"UPDATE printings SET {assignments} WHERE set_code = ? AND name = ?",
            (*prices.values(), set_code, name),
        )
        catalog.commit()


@pytest.mark.parametrize(
    ("finish", "prices", "currency", "expected_price"),
    [
        ("nonfoil", {"eur": 2.0, "eur_foil": 9.0}, "eur", 2.0),
        ("foil", {"eur": 2.0, "eur_foil": 9.0}, "eur", 9.0),
        ("foil", {"eur": 2.0, "eur_foil": None}, "eur", 2.0),  # falls back to non-foil
        ("etched", {"eur": 2.0, "eur_foil": 9.0}, "eur", 9.0),  # etched uses the foil price
        ("etched", {"eur": 2.0, "eur_foil": None}, "eur", 2.0),
        ("nonfoil", {"eur": None, "eur_foil": 9.0}, "eur", None),  # no fallback upwards
        ("foil", {"eur": None, "eur_foil": None}, "eur", None),
        ("nonfoil", {"usd": 3.0, "usd_foil": 8.0, "usd_etched": 20.0}, "usd", 3.0),
        ("foil", {"usd": 3.0, "usd_foil": 8.0, "usd_etched": 20.0}, "usd", 8.0),
        ("etched", {"usd": 3.0, "usd_foil": 8.0, "usd_etched": 20.0}, "usd", 20.0),
        ("etched", {"usd": 3.0, "usd_foil": 8.0, "usd_etched": None}, "usd", 8.0),
        ("etched", {"usd": 3.0, "usd_foil": None, "usd_etched": None}, "usd", 3.0),
    ],
)
def test_value_follows_the_finish(catalog, cards, finish, prices, currency, expected_price):
    reprice(catalog, "c21", "Sol Ring", **prices)
    with closing(db.connect(catalog)) as conn:
        service.add_entry(conn, cards[("Sol Ring", "c21")]["id"], 3, finish=finish)
        rows = stats.collect(conn, currency=currency)
        total = stats.totals(rows)
        assert rows[0]["price"] == expected_price
        if expected_price is None:
            assert (total.value, total.unpriced) == (0, 3)
            assert stats.most_valuable(rows) == []
        else:
            assert (total.value, total.unpriced) == (3 * expected_price, 0)


# --- breakdowns -----------------------------------------------------------------------


def test_by_colour(owned, cards):
    def key(card, finish, place):
        found = colours(card)
        return ["c"] if not found else [found.pop().lower()] if len(found) == 1 else ["m"]

    bars = stats.breakdowns(stats.collect(owned))["color"]
    assert as_dict(bars) == expected(cards, key)
    assert [bar.key for bar in bars] == [k for k in "wubrgmc" if k in as_dict(bars)]
    assert as_dict(bars)["r"] == (4, 1)  # four Bolts, one card
    assert as_dict(bars)["m"] == (1, 1) and as_dict(bars)["c"] == (14, 2)


def test_by_rarity(owned, cards):
    bars = stats.breakdowns(stats.collect(owned))["rarity"]
    assert as_dict(bars) == expected(cards, lambda card, f, p: [card["rarity"]])
    order = ["common", "uncommon", "rare", "special", "mythic", "bonus"]
    assert [bar.key for bar in bars] == [k for k in order if k in as_dict(bars)]


def test_by_type_counts_a_card_under_each_of_its_types(owned, cards):
    bars = stats.breakdowns(stats.collect(owned))["type"]
    assert as_dict(bars) == expected(cards, lambda card, f, p: front_types(card))
    types = as_dict(bars)
    assert types["Land"] == (11, 2)  # Mountains and the Dryad Arbor
    assert types["Creature"][1] == 4  # Angel, Niv-Mizzet, Dryad Arbor, Delver (front face)
    assert "Legendary" not in types and "Basic" not in types
    assert [bar.key for bar in bars][:2] == ["Creature", "Planeswalker"]


def test_mana_curve_leaves_lands_out(owned, cards):
    def key(card, finish, place):
        if "Land" in front_types(card):
            return []
        return [str(int(card["cmc"])) if card["cmc"] < 7 else "7+"]

    bars = stats.breakdowns(stats.collect(owned))["mana_value"]
    assert as_dict(bars) == expected(cards, key)
    assert sum(bar.copies for bar in bars) == 24 - 11
    assert [bar.key for bar in bars] == sorted(bar.key for bar in bars)


def test_by_year_of_the_printing(owned, cards):
    bars = stats.breakdowns(stats.collect(owned))["year"]
    assert as_dict(bars) == expected(cards, lambda card, f, p: [card["released_at"][:4]])
    assert bars[0].key == "1993" and [b.key for b in bars] == sorted(b.key for b in bars)
    assert as_dict(bars)["1993"] == (1, 1) and as_dict(bars)["2009"] == (3, 1)


def test_by_finish_condition_language_location(owned, cards):
    service.update_entry(owned, 5, condition="EX", language="it")  # the Sol Rings
    charts = stats.breakdowns(stats.collect(owned))
    assert as_dict(charts["finish"]) == {"nonfoil": (22, 7), "foil": (2, 1)}
    assert as_dict(charts["condition"]) == {"NM": (20, 7), "EX": (4, 1)}
    assert [bar.key for bar in charts["condition"]] == ["NM", "EX"]  # best first
    assert as_dict(charts["language"]) == {"en": (20, 7), "it": (4, 1)}
    assert as_dict(charts["location"]) == {"Binder": (6, 2), "Deck": (15, 3), "": (3, 3)}
    assert [bar.key for bar in charts["location"]] == ["Binder", "Deck", ""]


def test_location_order_follows_the_owner(owned):
    service.shift_location(owned, 2, -1)  # Deck before Binder
    keys = [bar.key for bar in stats.breakdowns(stats.collect(owned))["location"]]
    assert keys == ["Deck", "Binder", ""]


def test_one_location_only(owned):
    binder = stats.collect(owned, 1)
    assert (stats.totals(binder).copies, stats.totals(binder).cards) == (6, 2)
    assert as_dict(stats.breakdowns(binder)["color"]) == {"w": (2, 1), "r": (4, 1)}
    loose = stats.collect(owned, service.UNASSIGNED)
    assert stats.totals(loose).copies == 3
    assert [bar.key for bar in stats.breakdowns(loose)["location"]] == [""]
    assert stats.totals(stats.collect(owned, 99)).entries == 0


# --- most valuable, most recent -------------------------------------------------------


def test_most_valuable(owned, cards):
    rows = stats.collect(owned)
    top = stats.most_valuable(rows)
    values = [row["quantity"] * row["price"] for row in top]
    assert values == sorted(values, reverse=True) and len(top) == len(
        [r for r in rows if r["price"]]
    )
    lotus_like = max(rows, key=lambda r: (r["price"] or 0) * r["quantity"])
    assert top[0]["id"] == lotus_like["id"]
    assert len(stats.most_valuable(rows, 2)) == 2


def test_recently_added(owned):
    recent = stats.recently_added(stats.collect(owned), 3)
    assert [row["name"] for row in recent] == [
        "Jace, the Mind Sculptor",
        "Mountain",
        "Delver of Secrets // Insectile Aberration",
    ]


# --- set completion -------------------------------------------------------------------


def arabian(fixture_cards):
    return [c for c in fixture_cards if c["set"] == "arn"]


def progress(conn, code="arn", **kwargs):
    return next((s for s in stats.set_completion(conn, **kwargs) if s.code == code), None)


def test_set_with_nothing_owned_is_not_listed(owned):
    assert progress(owned) is None
    assert {s.code for s in stats.set_completion(owned)} == {name[1] for name in OWNED}


def test_partial_set(conn, fixture_cards):
    first, second = arabian(fixture_cards)[:2]
    service.add_entry(conn, first["id"], 3)
    service.add_entry(conn, first["id"], 1, condition="EX")  # same card again: still one
    service.add_entry(conn, second["id"], 1)
    arn = progress(conn)
    assert (arn.owned, arn.total, arn.percent, arn.complete) == (2, 92, 2, False)
    assert (arn.name, arn.set_type, arn.released_at) == (
        "Arabian Nights",
        "expansion",
        "1993-12-17",
    )


def test_complete_set(conn, fixture_cards):
    for card in arabian(fixture_cards)[:-1]:
        service.add_entry(conn, card["id"])
    almost = progress(conn)
    assert (almost.owned, almost.percent, almost.complete) == (91, 98, False)  # never rounds up
    service.add_entry(conn, arabian(fixture_cards)[-1]["id"])
    done = progress(conn)
    assert (done.owned, done.total, done.percent, done.complete) == (92, 92, 100, True)


def test_missing_link_lists_exactly_what_is_missing(conn, fixture_cards):
    cards = arabian(fixture_cards)
    have = cards[::3]
    for card in have:
        service.add_entry(conn, card["id"])
    result = search.run(conn, stats.missing_query("arn"), per_page=1000)
    assert result.unique == "prints" and result.order == "set"
    assert {row["id"] for row in result.rows} == {c["id"] for c in cards} - {c["id"] for c in have}
    assert result.total == 92 - progress(conn).owned


def owned_name(conn, location_id):
    return conn.execute("SELECT name FROM locations WHERE id = ?", (location_id,)).fetchone()[0]


def test_missing_link_for_one_location(conn, fixture_cards):
    cards = arabian(fixture_cards)
    binder = service.create_location(conn, 'Binder "A"', "binder")
    service.add_entry(conn, cards[0]["id"], location_id=binder)
    service.add_entry(conn, cards[1]["id"])  # owned, but somewhere else
    assert progress(conn, location=binder).owned == 1 and progress(conn).owned == 2
    name = owned_name(conn, binder)
    assert name == "Binder 'A'"  # a double quote in a name becomes an apostrophe
    query = stats.missing_query("arn", name)
    result = search.run(conn, query, per_page=1000)  # the quote in the name does not break it
    assert {row["id"] for row in result.rows} == {c["id"] for c in cards[1:]}


def test_digital_sets_are_left_out(conn, fixture_cards):
    digital = next(c for c in fixture_cards if c["set"] == "j21")
    service.add_entry(conn, digital["id"])
    assert stats.set_completion(conn) == []
    assert stats.totals(stats.collect(conn)).copies == 1  # still counted as a card


def test_set_sorting_and_type_filter(owned, fixture_sets):
    by_percent = stats.set_completion(owned)
    ratios = [s.owned / s.total for s in by_percent]
    assert ratios == sorted(ratios, reverse=True)
    by_name = stats.set_completion(owned, sort="name")
    assert [s.name.lower() for s in by_name] == sorted(s.name.lower() for s in by_name)
    by_date = stats.set_completion(owned, sort="date")
    assert [s.released_at for s in by_date] == sorted(
        (s.released_at for s in by_date), reverse=True
    )
    assert stats.set_completion(owned, sort="nonsense") == by_percent

    kinds = stats.set_types(owned)
    assert "core" in kinds and "expansion" in kinds and kinds == sorted(kinds)
    core = stats.set_completion(owned, set_type="core")
    assert core and {s.set_type for s in core} == {"core"}
    assert stats.set_completion(owned, set_type="nonsense") == []
    assert {s.code for s in stats.set_completion(owned, location=1)} == {"m10", "lea", "dom"}


# --- the page -------------------------------------------------------------------------


@pytest.fixture
def client(catalog, owned):
    return TestClient(create_app(catalog))


def test_stats_page(client):
    page = client.get("/stats")
    assert page.status_code == 200
    text = page.text
    for expected_text in [
        "<dd>24</dd>",  # cards
        "<dd>8</dd>",  # different cards
        "By colour",
        "By mana value (lands excluded)",
        "Set completion (9 sets)",
        "Most valuable",
        "Added most recently",
        ">Multicolour<",
        "NM · Near Mint",
        ">No location<",
    ]:
        assert expected_text in text, expected_text
    assert 'class="bar color-r" style="width: ' in text
    assert 'style="width: 100.0%"' in text  # the longest bar of a chart is full width
    assert "set%3Am10+haveprint%3Ano+unique%3Aprints+order%3Aset" in text  # the missing link
    assert "Jace, the Mind Sculptor" in text
    assert 'href="/stats"' in client.get("/collection").text


def test_stats_page_for_one_location(client):
    page = client.get("/stats?location=1").text
    assert "<dd>6</dd>" in page and "Set completion (3 sets)" in page
    assert '<option value="1" selected>Binder</option>' in page
    assert "-loc%3A%22Binder%22" in page  # missing = not in this binder
    loose = client.get("/stats?location=none").text
    assert "<dd>3</dd>" in loose and '<option value="none" selected>' in loose
    assert "There is nothing here yet." in client.get("/stats?location=99").text
    assert "<dd>24</dd>" in client.get("/stats?location=garbage").text


def test_stats_page_set_options(client):
    core = client.get("/stats?set_type=core&sets=name").text
    assert '<option value="core" selected>' in core
    assert (
        "Magic 2010" in core
        and "Dominaria" not in core.split('id="sets"')[1].split("Most valuable")[0]
    )
    assert "sets=name" in core and 'aria-current="true">By name' in core
    assert client.get("/stats?set_type=nonsense&sets=nonsense").status_code == 200


def test_stats_page_when_empty_or_without_catalog(catalog, settings, tmp_path):
    empty = TestClient(create_app(catalog)).get("/stats")
    assert empty.status_code == 200 and "There is nothing here yet." in empty.text
    assert "Set completion" not in empty.text
    bare = TestClient(
        create_app(settings.__class__(**{**settings.__dict__, "data_dir": tmp_path / "x"}))
    )
    assert bare.get("/stats").status_code == 503


def test_stats_page_in_italian(client):
    client.get("/lang/it")
    page = client.get("/stats").text
    for expected_text in [
        "Statistiche",
        "Per colore",
        "Completamento dei set (9 set)",
        "Le più preziose",
    ]:
        assert expected_text in page
