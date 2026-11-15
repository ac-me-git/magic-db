"""Collection service: one row per printing and finish, merge rules, history (SPEC §5)."""

import json
from contextlib import closing

import pytest

from magicdb import db
from magicdb.collection import service
from magicdb.collection.service import CollectionError


@pytest.fixture
def conn(catalog):
    with closing(db.connect(catalog)) as connection:
        yield connection


def pid(conn, name, set_code):
    return conn.execute(
        "SELECT id FROM cat.printings WHERE name = ? AND set_code = ?", (name, set_code)
    ).fetchone()[0]


@pytest.fixture
def bolt(conn):
    return pid(conn, "Lightning Bolt", "m10")


def entries(conn):
    rows = conn.execute("SELECT name, set_code, quantity, finish FROM entries ORDER BY id")
    return [tuple(row) for row in rows]


def history(conn):
    rows = conn.execute("SELECT action, detail FROM history ORDER BY id")
    return [(row["action"], json.loads(row["detail"])) for row in rows]


def code(excinfo):
    return excinfo.value.code


# --- add ------------------------------------------------------------------------------


def test_add_copies_card_identity_from_the_catalog(conn, bolt):
    added = service.add_entry(conn, bolt)
    assert (added.merged, added.unusual_finish) == (False, False)
    row = service.get_entry(conn, added.entry_id)
    assert (row["name"], row["set_code"], row["collector_number"]) == (
        "Lightning Bolt",
        "m10",
        "146",
    )
    assert row["oracle_id"] and row["scryfall_id"] == bolt
    assert (row["quantity"], row["finish"]) == (1, "nonfoil")
    assert row["created_at"] == row["updated_at"]


def test_an_entry_has_no_condition_language_or_location(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(entries)")}
    assert not columns & {"condition", "language", "location_id"}
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "locations" not in tables


def test_adding_the_same_printing_and_finish_twice_merges(conn, bolt):
    first = service.add_entry(conn, bolt, 2)
    second = service.add_entry(conn, bolt, 3)
    assert second.merged and second.entry_id == first.entry_id
    assert entries(conn) == [("Lightning Bolt", "m10", 5, "nonfoil")]


@pytest.mark.parametrize("different", [{"finish": "foil"}, {"other": 1}])
def test_another_finish_or_printing_is_a_new_row(conn, bolt, different):
    service.add_entry(conn, bolt)
    if "other" in different:
        service.add_entry(conn, pid(conn, "Lightning Bolt", "lea"))
    else:
        service.add_entry(conn, bolt, **different)
    assert len(entries(conn)) == 2


def test_merging_keeps_notes_and_price(conn, bolt):
    service.add_entry(conn, bolt, notes="signed", purchase_price="1,50")
    merged = service.add_entry(conn, bolt, notes="from Marco")
    row = service.get_entry(conn, merged.entry_id)
    assert row["notes"] == "signed; from Marco" and row["purchase_price"] == 1.5
    service.add_entry(conn, bolt, notes="signed", purchase_price=2)
    row = service.get_entry(conn, merged.entry_id)
    assert row["notes"] == "signed; from Marco" and row["purchase_price"] == 2.0


def test_unusual_finish_is_allowed_but_flagged(conn):
    alpha = pid(conn, "Lightning Bolt", "lea")  # Alpha was never printed in foil
    added = service.add_entry(conn, alpha, finish="foil")
    assert added.unusual_finish and entries(conn)[0][3] == "foil"


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"quantity": 0}, "bad_quantity"),
        ({"quantity": -1}, "bad_quantity"),
        ({"quantity": "many"}, "bad_quantity"),
        ({"quantity": 10000}, "bad_quantity"),
        ({"finish": "shiny"}, "bad_finish"),
        ({"purchase_price": "free"}, "bad_price"),
        ({"purchase_price": -1}, "bad_price"),
    ],
)
def test_add_rejects_bad_input_and_writes_nothing(conn, bolt, kwargs, expected):
    with pytest.raises(CollectionError) as excinfo:
        service.add_entry(conn, bolt, **kwargs)
    assert code(excinfo) == expected
    assert entries(conn) == [] and history(conn) == []


def test_add_unknown_card(conn):
    with pytest.raises(CollectionError) as excinfo:
        service.add_entry(conn, "00000000-0000-4000-8000-000000000000")
    assert code(excinfo) == "unknown_card"


def test_add_needs_the_catalog(settings):
    db.migrate(settings)
    with closing(db.connect(settings)) as conn, pytest.raises(CollectionError) as excinfo:
        service.add_entry(conn, "x")
    assert code(excinfo) == "no_catalog"


# --- edit -----------------------------------------------------------------------------


def test_edit(conn, bolt):
    entry = service.add_entry(conn, bolt, 2).entry_id
    result = service.update_entry(
        conn, entry, quantity=4, finish="foil", notes=" altered ", purchase_price="3.20"
    )
    assert result == entry
    assert entries(conn) == [("Lightning Bolt", "m10", 4, "foil")]
    row = service.get_entry(conn, entry)
    assert row["notes"] == "altered" and row["purchase_price"] == 3.2
    assert row["updated_at"] >= row["created_at"]


def test_edit_into_a_collision_merges(conn, bolt):
    keep = service.add_entry(conn, bolt, 2).entry_id
    foil = service.add_entry(conn, bolt, 3, finish="foil").entry_id
    result = service.update_entry(conn, foil, finish="nonfoil")
    assert result == keep
    assert entries(conn) == [("Lightning Bolt", "m10", 5, "nonfoil")]
    assert service.get_entry(conn, foil) is None


def test_edit_collision_uses_the_new_quantity(conn, bolt):
    keep = service.add_entry(conn, bolt, 2).entry_id
    other = service.add_entry(conn, bolt, 3, finish="foil").entry_id
    assert service.update_entry(conn, other, quantity=1, finish="nonfoil") == keep
    assert entries(conn) == [("Lightning Bolt", "m10", 3, "nonfoil")]


def test_edit_with_nothing_changed_writes_nothing(conn, bolt):
    entry = service.add_entry(conn, bolt, 2).entry_id
    before = len(history(conn))
    assert service.update_entry(conn, entry, quantity=2, finish="nonfoil") == entry
    assert len(history(conn)) == before


@pytest.mark.parametrize(
    ("changes", "expected"),
    [({"quantity": 0}, "bad_quantity"), ({"finish": "x"}, "bad_finish")],
)
def test_edit_rejects_bad_input(conn, bolt, changes, expected):
    entry = service.add_entry(conn, bolt, 2).entry_id
    with pytest.raises(CollectionError) as excinfo:
        service.update_entry(conn, entry, **changes)
    assert code(excinfo) == expected
    assert entries(conn)[0][2] == 2


def test_edit_unknown_entry_or_field(conn, bolt):
    with pytest.raises(CollectionError) as excinfo:
        service.update_entry(conn, 99, quantity=1)
    assert code(excinfo) == "unknown_entry"
    entry = service.add_entry(conn, bolt).entry_id
    for field in ("name", "condition", "language", "location_id"):
        with pytest.raises(TypeError):
            service.update_entry(conn, entry, **{field: "x"})


# --- remove ---------------------------------------------------------------------------


def test_remove(conn, bolt):
    entry = service.add_entry(conn, bolt, 5).entry_id
    service.remove_entry(conn, entry, 2)
    assert entries(conn)[0][2] == 3
    service.remove_entry(conn, entry)  # the rest
    assert entries(conn) == []
    with pytest.raises(CollectionError) as excinfo:
        service.remove_entry(conn, entry)
    assert code(excinfo) == "unknown_entry"


def test_quantity_never_reaches_zero(conn, bolt):
    entry = service.add_entry(conn, bolt, 2).entry_id
    for quantity, expected in [(3, "too_many"), (0, "bad_quantity"), (-1, "bad_quantity")]:
        with pytest.raises(CollectionError) as excinfo:
            service.remove_entry(conn, entry, quantity)
        assert code(excinfo) == expected
    service.remove_entry(conn, entry, 2)  # exactly all of them: the row goes away
    assert conn.execute("SELECT COUNT(*) FROM entries WHERE quantity <= 0").fetchone()[0] == 0
    assert entries(conn) == []


def test_take_one_removes_a_copy_of_that_finish(conn, bolt):
    service.add_entry(conn, bolt, 2)
    service.add_entry(conn, bolt, 1, finish="foil")
    assert service.take_one(conn, bolt, finish="foil")
    assert not service.take_one(conn, bolt, finish="foil")  # none left
    assert not service.take_one(conn, bolt, finish="etched")
    assert entries(conn) == [("Lightning Bolt", "m10", 2, "nonfoil")]


def test_copies_by_finish(conn, bolt):
    alpha = pid(conn, "Lightning Bolt", "lea")
    service.add_entry(conn, bolt, 2)
    service.add_entry(conn, bolt, 1, finish="foil")
    assert service.copies_by_finish(conn, [bolt, alpha]) == {
        (bolt, "nonfoil"): 2,
        (bolt, "foil"): 1,
    }
    assert service.copies_by_finish(conn, []) == {}


# --- history --------------------------------------------------------------------------


def test_every_write_adds_exactly_one_history_row(conn, bolt):
    steps = []

    def step(action, call):
        result = call()
        steps.append(action)
        assert [a for a, _ in history(conn)] == steps
        return result

    entry = step("add", lambda: service.add_entry(conn, bolt, 4)).entry_id
    step("add", lambda: service.add_entry(conn, bolt, 1))  # merge
    step("edit", lambda: service.update_entry(conn, entry, finish="foil"))
    step("remove", lambda: service.remove_entry(conn, entry, 1))
    step("import", lambda: service.import_entries(conn, []))


def test_history_says_what_happened(conn, bolt):
    entry = service.add_entry(conn, bolt, 4).entry_id
    service.update_entry(conn, entry, finish="foil")
    log = history(conn)
    assert log[0] == (
        "add",
        {
            "entry_id": entry,
            "name": "Lightning Bolt",
            "set_code": "m10",
            "collector_number": "146",
            "merged": False,
            "quantity": 4,
            "finish": "nonfoil",
        },
    )
    assert log[1] == (
        "edit",
        {
            "entry_id": entry,
            "name": "Lightning Bolt",
            "before": {"finish": "nonfoil"},
            "after": {"finish": "foil"},
            "merged_into": None,
        },
    )


def test_a_failed_write_leaves_no_trace(conn, bolt):
    entry = service.add_entry(conn, bolt, 2).entry_id
    before = (entries(conn), history(conn))
    for call in [
        lambda: service.remove_entry(conn, entry, 9),
        lambda: service.update_entry(conn, entry, quantity=3, finish="shiny"),
        lambda: service.add_entry(conn, bolt, 0),
    ]:
        with pytest.raises(CollectionError):
            call()
    assert (entries(conn), history(conn)) == before
    assert not conn.in_transaction


# --- import ---------------------------------------------------------------------------


def test_import_merges_rows_of_the_same_printing_and_finish(conn, bolt):
    card = {
        "scryfall_id": bolt,
        "oracle_id": conn.execute(
            "SELECT oracle_id FROM cat.printings WHERE id = ?", (bolt,)
        ).fetchone()[0],
        "name": "Lightning Bolt",
        "set_code": "m10",
        "collector_number": "146",
    }
    items = [
        {"card": card, "quantity": 2, "finish": "nonfoil", "added_at": "2024-05-01T00:00:00+00:00"},
        {"card": card, "quantity": 3, "finish": "nonfoil", "added_at": "2023-01-01T00:00:00+00:00"},
        {"card": card, "quantity": 1, "finish": "foil"},
    ]
    result = service.import_entries(conn, items, source="test")
    assert (result["entries"], result["copies"]) == (3, 6)
    assert entries(conn) == [
        ("Lightning Bolt", "m10", 5, "nonfoil"),
        ("Lightning Bolt", "m10", 1, "foil"),
    ]
    first = conn.execute("SELECT created_at FROM entries ORDER BY id").fetchone()[0]
    assert first == "2023-01-01T00:00:00+00:00"  # the earliest "added on" is kept
    service.import_entries(conn, items[:1], replace=True)
    assert entries(conn) == [("Lightning Bolt", "m10", 2, "nonfoil")]


# --- reads ----------------------------------------------------------------------------


@pytest.fixture
def stocked(conn):
    service.add_entry(conn, pid(conn, "Lightning Bolt", "m10"), 3)
    service.add_entry(conn, pid(conn, "Lightning Bolt", "lea"), 1)
    service.add_entry(conn, pid(conn, "Serra Angel", "dom"), 4, finish="foil")
    service.add_entry(conn, pid(conn, "Sol Ring", "c21"), 2)
    return conn


def test_values_follow_the_finish(stocked, fixture_cards):
    def price(name, set_code, key):
        card = next(c for c in fixture_cards if c["name"] == name and c["set"] == set_code)
        value = card["prices"].get(key)
        return float(value) if value else None

    def value(name, set_code, currency="eur"):
        entry = stocked.execute(
            "SELECT id FROM entries WHERE name = ? AND set_code = ?", (name, set_code)
        ).fetchone()["id"]
        return service.totals(stocked, currency, "e.id = ?", (entry,))["value"]

    assert value("Lightning Bolt", "m10") == pytest.approx(
        3 * price("Lightning Bolt", "m10", "eur")
    )
    # foil copies use the foil price, or the plain one when there is none
    each = price("Serra Angel", "dom", "eur_foil") or price("Serra Angel", "dom", "eur")
    assert value("Serra Angel", "dom") == (pytest.approx(4 * each) if each else None)
    dollars = price("Lightning Bolt", "m10", "usd")
    assert value("Lightning Bolt", "m10", "usd") == pytest.approx(3 * dollars)


def test_copies_of_card_spans_printings(stocked):
    oracle = stocked.execute(
        "SELECT oracle_id FROM cat.printings WHERE name = 'Lightning Bolt' LIMIT 1"
    ).fetchone()[0]
    copies = service.copies_of_card(stocked, oracle)
    assert [(row["set_code"], row["quantity"]) for row in copies] == [("lea", 1), ("m10", 3)]


def test_owned_counts(stocked):
    m10, lea = pid(stocked, "Lightning Bolt", "m10"), pid(stocked, "Lightning Bolt", "lea")
    a25 = pid(stocked, "Lightning Bolt", "a25")
    assert service.owned_counts(stocked, [m10, lea, a25], "prints") == {m10: 3, lea: 1}
    oracle = stocked.execute("SELECT oracle_id FROM cat.printings WHERE id = ?", (m10,)).fetchone()[
        0
    ]
    assert service.owned_counts(stocked, [oracle, "nothing"], "cards") == {oracle: 4}
    assert service.owned_counts(stocked, [], "cards") == {}


def test_reads_work_without_a_catalog(settings):
    db.migrate(settings)
    with closing(db.connect(settings)) as conn:
        conn.execute(
            "INSERT INTO entries (scryfall_id, oracle_id, name, set_code, collector_number,"
            " quantity, created_at, updated_at)"
            " VALUES ('p', 'o', 'Lightning Bolt', 'm10', '146', 2, 'now', 'now')"
        )
        total = service.totals(conn)
        assert (total["entries"], total["copies"], total["value"]) == (1, 2, None)
        entry = conn.execute("SELECT id FROM entries").fetchone()["id"]
        service.remove_entry(conn, entry, 1)  # removing needs no catalog either
        assert service.totals(conn)["copies"] == 1
