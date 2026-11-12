"""Collection service: merge rules, moves, locations, history (SPEC §5)."""

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


@pytest.fixture
def places(conn):
    return {
        name: service.create_location(conn, name, kind)
        for name, kind in [("Binder 1", "binder"), ("Blue box", "box"), ("Dragons", "deck")]
    }


def entries(conn):
    rows = conn.execute(
        "SELECT e.name, e.set_code, e.quantity, e.finish, e.condition, e.language, l.name "
        "FROM entries e LEFT JOIN locations l ON l.id = e.location_id ORDER BY e.id"
    )
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
    assert (row["quantity"], row["finish"], row["condition"], row["language"]) == (
        1,
        "nonfoil",
        "NM",
        "en",
    )
    assert row["location_id"] is None and row["location_name"] is None
    assert row["created_at"] == row["updated_at"]


def test_adding_the_same_thing_twice_merges(conn, bolt, places):
    first = service.add_entry(conn, bolt, 2, location_id=places["Binder 1"])
    second = service.add_entry(conn, bolt, 3, location_id=places["Binder 1"])
    assert second.merged and second.entry_id == first.entry_id
    assert entries(conn) == [("Lightning Bolt", "m10", 5, "nonfoil", "NM", "en", "Binder 1")]


@pytest.mark.parametrize(
    "different",
    [
        {"finish": "foil"},
        {"condition": "EX"},
        {"language": "it"},
        {"location_id": "box"},
        {"other": 1},
    ],
)
def test_anything_different_is_a_new_row(conn, bolt, places, different):
    service.add_entry(conn, bolt, location_id=places["Binder 1"])
    if "other" in different:
        service.add_entry(conn, pid(conn, "Lightning Bolt", "lea"), location_id=places["Binder 1"])
    else:
        if "location_id" in different:
            different = {"location_id": places["Blue box"]}
        service.add_entry(conn, bolt, **{"location_id": places["Binder 1"], **different})
    assert len(entries(conn)) == 2


def test_unassigned_copies_merge_with_each_other(conn, bolt):
    service.add_entry(conn, bolt)
    assert service.add_entry(conn, bolt, 2).merged
    assert entries(conn) == [("Lightning Bolt", "m10", 3, "nonfoil", "NM", "en", None)]


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
        ({"condition": "MP"}, "bad_condition"),
        ({"language": "tlh"}, "bad_language"),
        ({"location_id": 99}, "unknown_location"),
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


def test_edit(conn, bolt, places):
    entry = service.add_entry(conn, bolt, 2).entry_id
    result = service.update_entry(
        conn,
        entry,
        quantity=4,
        finish="foil",
        condition="EX",
        language="it",
        location_id=places["Dragons"],
        notes=" altered ",
        purchase_price="3.20",
    )
    assert result == entry
    assert entries(conn) == [("Lightning Bolt", "m10", 4, "foil", "EX", "it", "Dragons")]
    row = service.get_entry(conn, entry)
    assert row["notes"] == "altered" and row["purchase_price"] == 3.2
    assert row["updated_at"] >= row["created_at"]


def test_edit_into_a_collision_merges(conn, bolt, places):
    keep = service.add_entry(conn, bolt, 2, location_id=places["Binder 1"]).entry_id
    foil = service.add_entry(conn, bolt, 3, finish="foil", location_id=places["Binder 1"]).entry_id
    result = service.update_entry(conn, foil, finish="nonfoil")
    assert result == keep
    assert entries(conn) == [("Lightning Bolt", "m10", 5, "nonfoil", "NM", "en", "Binder 1")]
    assert service.get_entry(conn, foil) is None


def test_edit_collision_uses_the_new_quantity(conn, bolt, places):
    keep = service.add_entry(conn, bolt, 2, location_id=places["Binder 1"]).entry_id
    other = service.add_entry(conn, bolt, 3, location_id=places["Blue box"]).entry_id
    assert service.update_entry(conn, other, quantity=1, location_id=places["Binder 1"]) == keep
    assert entries(conn) == [("Lightning Bolt", "m10", 3, "nonfoil", "NM", "en", "Binder 1")]


def test_edit_with_nothing_changed_writes_nothing(conn, bolt):
    entry = service.add_entry(conn, bolt, 2).entry_id
    before = len(history(conn))
    assert service.update_entry(conn, entry, quantity=2, finish="nonfoil") == entry
    assert len(history(conn)) == before


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"quantity": 0}, "bad_quantity"),
        ({"finish": "x"}, "bad_finish"),
        ({"condition": "x"}, "bad_condition"),
        ({"language": "x"}, "bad_language"),
        ({"location_id": 99}, "unknown_location"),
    ],
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
    with pytest.raises(TypeError):
        service.update_entry(conn, entry, name="Black Lotus")


# --- move -----------------------------------------------------------------------------


def test_partial_move(conn, bolt, places):
    entry = service.add_entry(
        conn, bolt, 5, condition="EX", location_id=places["Binder 1"]
    ).entry_id
    moved = service.move_entry(conn, entry, 2, places["Dragons"])
    assert moved != entry
    assert entries(conn) == [
        ("Lightning Bolt", "m10", 3, "nonfoil", "EX", "en", "Binder 1"),
        ("Lightning Bolt", "m10", 2, "nonfoil", "EX", "en", "Dragons"),
    ]


def test_full_move(conn, bolt, places):
    entry = service.add_entry(conn, bolt, 5, location_id=places["Binder 1"]).entry_id
    added = service.get_entry(conn, entry)["created_at"]
    conn.execute("UPDATE entries SET created_at = '2020-01-01T00:00:00+00:00'")
    # a whole row with nothing to merge into just changes place
    assert service.move_entry(conn, entry, 5, places["Dragons"]) == entry
    assert entries(conn) == [("Lightning Bolt", "m10", 5, "nonfoil", "NM", "en", "Dragons")]
    row = service.get_entry(conn, entry)
    assert row["created_at"] == "2020-01-01T00:00:00+00:00" != added  # "added on" survives


def test_move_merges_at_the_destination(conn, bolt, places):
    source = service.add_entry(conn, bolt, 5, location_id=places["Binder 1"]).entry_id
    there = service.add_entry(conn, bolt, 1, location_id=places["Dragons"]).entry_id
    assert service.move_entry(conn, source, 3, places["Dragons"]) == there
    assert sorted(e[2:] for e in entries(conn)) == [
        (2, "nonfoil", "NM", "en", "Binder 1"),
        (4, "nonfoil", "NM", "en", "Dragons"),
    ]


def test_move_to_and_from_unassigned(conn, bolt, places):
    entry = service.add_entry(conn, bolt, 2).entry_id
    placed = service.move_entry(conn, entry, 2, places["Blue box"])
    assert entries(conn)[0][6] == "Blue box"
    service.move_entry(conn, placed, 1, None)
    assert sorted(e[6] or "" for e in entries(conn)) == ["", "Blue box"]


def test_move_to_the_same_location_is_a_no_op(conn, bolt, places):
    entry = service.add_entry(conn, bolt, 2, location_id=places["Binder 1"]).entry_id
    loose = service.add_entry(conn, pid(conn, "Sol Ring", "c21")).entry_id
    before = (entries(conn), history(conn))
    assert service.move_entry(conn, entry, 1, places["Binder 1"]) == entry
    assert service.move_entry(conn, loose, 1, None) == loose
    assert (entries(conn), history(conn)) == before


@pytest.mark.parametrize(("quantity", "expected"), [(6, "too_many"), (0, "bad_quantity")])
def test_move_rejects_bad_quantities(conn, bolt, places, quantity, expected):
    entry = service.add_entry(conn, bolt, 5, location_id=places["Binder 1"]).entry_id
    with pytest.raises(CollectionError) as excinfo:
        service.move_entry(conn, entry, quantity, places["Dragons"])
    assert code(excinfo) == expected
    assert len(entries(conn)) == 1 and entries(conn)[0][2] == 5


def test_move_to_unknown_location(conn, bolt):
    entry = service.add_entry(conn, bolt).entry_id
    with pytest.raises(CollectionError) as excinfo:
        service.move_entry(conn, entry, 1, 99)
    assert code(excinfo) == "unknown_location"


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


# --- history --------------------------------------------------------------------------


def test_every_write_adds_exactly_one_history_row(conn, bolt):
    steps = []

    def step(action, call):
        result = call()
        steps.append(action)
        assert [a for a, _ in history(conn)] == steps
        return result

    binder = step("location", lambda: service.create_location(conn, "Binder 1", "binder"))
    box = step("location", lambda: service.create_location(conn, "Box"))
    entry = step("add", lambda: service.add_entry(conn, bolt, 4, location_id=binder)).entry_id
    step("add", lambda: service.add_entry(conn, bolt, 1, location_id=binder))  # merge
    step("edit", lambda: service.update_entry(conn, entry, condition="EX"))
    moved = step("move", lambda: service.move_entry(conn, entry, 2, box))
    step("remove", lambda: service.remove_entry(conn, moved, 1))
    step("location", lambda: service.update_location(conn, box, "Blue box", "box"))
    step("location", lambda: service.shift_location(conn, box, -1))
    step("location", lambda: service.empty_location(conn, binder, box))
    step("location", lambda: service.delete_location(conn, binder))


def test_history_says_what_happened(conn, bolt, places):
    entry = service.add_entry(conn, bolt, 4, location_id=places["Binder 1"]).entry_id
    service.move_entry(conn, entry, 3, places["Dragons"])
    service.update_entry(conn, entry, condition="GD")
    log = history(conn)[len(places) :]
    assert log[0] == (
        "add",
        {
            "entry_id": entry,
            "name": "Lightning Bolt",
            "set_code": "m10",
            "collector_number": "146",
            "location": "Binder 1",
            "merged": False,
            "quantity": 4,
            "finish": "nonfoil",
            "condition": "NM",
            "language": "en",
        },
    )
    assert log[1][0] == "move"
    assert {k: log[1][1][k] for k in ("quantity", "origin", "destination")} == {
        "quantity": 3,
        "origin": "Binder 1",
        "destination": "Dragons",
    }
    assert log[2] == (
        "edit",
        {
            "entry_id": entry,
            "name": "Lightning Bolt",
            "before": {"condition": "NM"},
            "after": {"condition": "GD"},
            "merged_into": None,
        },
    )


def test_a_failed_write_leaves_no_trace(conn, bolt, places):
    entry = service.add_entry(conn, bolt, 2, location_id=places["Binder 1"]).entry_id
    before = (entries(conn), history(conn))
    for call in [
        lambda: service.move_entry(conn, entry, 9, places["Dragons"]),
        lambda: service.update_entry(conn, entry, quantity=3, location_id=99),
        lambda: service.delete_location(conn, places["Binder 1"]),
        lambda: service.create_location(conn, "binder 1"),
    ]:
        with pytest.raises(CollectionError):
            call()
    assert (entries(conn), history(conn)) == before
    assert not conn.in_transaction


# --- locations ------------------------------------------------------------------------


def test_locations_keep_the_order_they_were_created_in(conn, places):
    assert [row["name"] for row in service.list_locations(conn)] == [
        "Binder 1",
        "Blue box",
        "Dragons",
    ]
    assert [row["kind"] for row in service.list_locations(conn)] == ["binder", "box", "deck"]


@pytest.mark.parametrize(
    ("name", "kind", "expected"),
    [
        ("", "box", "bad_location_name"),
        ("   ", "box", "bad_location_name"),
        ("x" * 81, "box", "bad_location_name"),
        ("Shelf", "drawer", "bad_kind"),
        ("BINDER 1", "box", "location_exists"),
        ("  binder   1 ", "box", "location_exists"),
    ],
)
def test_create_location_rejects(conn, places, name, kind, expected):
    with pytest.raises(CollectionError) as excinfo:
        service.create_location(conn, name, kind)
    assert code(excinfo) == expected
    assert len(service.list_locations(conn)) == 3


def test_update_location(conn, places):
    service.update_location(conn, places["Blue box"], "  Red   box ", "box", "under the desk")
    row = service.list_locations(conn)[1]
    assert (row["name"], row["notes"]) == ("Red box", "under the desk")
    service.update_location(conn, places["Blue box"], "RED BOX", "other")  # same name, new case
    with pytest.raises(CollectionError) as excinfo:
        service.update_location(conn, places["Blue box"], "dragons", "box")
    assert code(excinfo) == "location_exists"
    with pytest.raises(CollectionError) as excinfo:
        service.update_location(conn, 99, "x", "box")
    assert code(excinfo) == "unknown_location"


def test_shift_location(conn, places):
    def names():
        return [row["name"] for row in service.list_locations(conn)]

    service.shift_location(conn, places["Dragons"], -1)
    assert names() == ["Binder 1", "Dragons", "Blue box"]
    service.shift_location(conn, places["Binder 1"], 1)
    assert names() == ["Dragons", "Binder 1", "Blue box"]
    service.shift_location(conn, places["Dragons"], -1)  # already first
    service.shift_location(conn, places["Blue box"], 1)  # already last
    assert names() == ["Dragons", "Binder 1", "Blue box"]


def test_location_in_use_cannot_be_deleted(conn, bolt, places):
    service.add_entry(conn, bolt, 3, location_id=places["Binder 1"])
    with pytest.raises(CollectionError) as excinfo:
        service.delete_location(conn, places["Binder 1"])
    assert code(excinfo) == "location_in_use"
    assert excinfo.value.params == {"name": "Binder 1", "entries": 1}
    service.delete_location(conn, places["Dragons"])  # an empty one is fine
    assert [row["name"] for row in service.list_locations(conn)] == ["Binder 1", "Blue box"]


def test_empty_location_moves_everything_and_merges(conn, bolt, places):
    ring = pid(conn, "Sol Ring", "c21")
    service.add_entry(conn, bolt, 3, location_id=places["Binder 1"])
    service.add_entry(conn, ring, 1, location_id=places["Binder 1"])
    service.add_entry(conn, bolt, 2, location_id=places["Blue box"])
    assert service.empty_location(conn, places["Binder 1"], places["Blue box"]) == 4
    assert sorted(e[:3] + e[6:] for e in entries(conn)) == [
        ("Lightning Bolt", "m10", 5, "Blue box"),
        ("Sol Ring", "c21", 1, "Blue box"),
    ]
    service.delete_location(conn, places["Binder 1"])
    assert service.empty_location(conn, places["Blue box"], None) == 6
    assert {e[6] for e in entries(conn)} == {None}
    assert service.empty_location(conn, places["Blue box"], None) == 0  # nothing left
    assert service.empty_location(conn, places["Dragons"], places["Dragons"]) == 0


# --- reads ----------------------------------------------------------------------------


@pytest.fixture
def stocked(conn, places):
    service.add_entry(conn, pid(conn, "Lightning Bolt", "m10"), 3, location_id=places["Binder 1"])
    service.add_entry(
        conn, pid(conn, "Lightning Bolt", "lea"), 1, condition="PL", location_id=places["Blue box"]
    )
    service.add_entry(
        conn, pid(conn, "Serra Angel", "dom"), 4, finish="foil", location_id=places["Binder 1"]
    )
    service.add_entry(conn, pid(conn, "Sol Ring", "c21"), 2)
    return conn


def names(rows):
    return [(row["name"], row["set_code"]) for row in rows]


def test_list_entries_filters(stocked, places):
    rows, total = service.list_entries(stocked)
    assert len(rows) == 4 and (total["entries"], total["copies"]) == (4, 10)
    rows, total = service.list_entries(stocked, location=places["Binder 1"])
    assert sorted(names(rows)) == [("Lightning Bolt", "m10"), ("Serra Angel", "dom")]
    assert (total["entries"], total["copies"]) == (2, 7)
    rows, total = service.list_entries(stocked, location=service.UNASSIGNED)
    assert names(rows) == [("Sol Ring", "c21")] and total["copies"] == 2
    rows, total = service.list_entries(stocked, text="bolt")
    assert len(rows) == 2 and total["copies"] == 4
    assert service.list_entries(stocked, text="%")[0] == []
    rows, total = service.list_entries(stocked, location=places["Dragons"])
    assert rows == [] and (total["entries"], total["copies"], total["value"]) == (0, 0, None)


def test_list_entries_sorting_and_paging(stocked):
    def order(sort):
        return names(service.list_entries(stocked, sort=sort)[0])

    assert order("added")[0] == ("Sol Ring", "c21")  # newest first
    assert order("name") == sorted(order("name"))
    assert [n for n, _ in order("quantity")][:2] == ["Serra Angel", "Lightning Bolt"]
    assert order("set") == sorted(order("set"), key=lambda pair: pair[1])
    assert order("location")[-1] == ("Sol Ring", "c21")  # unassigned last
    assert order("nonsense") == order("added")
    values = [row["value"] for row in service.list_entries(stocked, sort="value")[0]]
    priced = [v for v in values if v is not None]
    assert priced == sorted(priced, reverse=True) and values[: len(priced)] == priced

    first, _ = service.list_entries(stocked, sort="name", per_page=3)
    second, total = service.list_entries(stocked, sort="name", page=2, per_page=3)
    assert len(first) == 3 and len(second) == 1 and total["entries"] == 4


def test_values_follow_the_finish(stocked, fixture_cards):
    def price(name, set_code, key):
        card = next(c for c in fixture_cards if c["name"] == name and c["set"] == set_code)
        value = card["prices"].get(key)
        return float(value) if value else None

    by_name = {(r["name"], r["set_code"]): r for r in service.list_entries(stocked)[0]}
    bolt = by_name[("Lightning Bolt", "m10")]
    assert bolt["price"] == price("Lightning Bolt", "m10", "eur")
    assert bolt["value"] == pytest.approx(3 * bolt["price"])
    angel = by_name[("Serra Angel", "dom")]  # foil copies use the foil price
    assert angel["price"] == price("Serra Angel", "dom", "eur_foil") or price(
        "Serra Angel", "dom", "eur"
    )
    dollars = {
        (r["name"], r["set_code"]): r for r in service.list_entries(stocked, currency="usd")[0]
    }
    assert dollars[("Lightning Bolt", "m10")]["price"] == price("Lightning Bolt", "m10", "usd")


def test_list_locations_counts(stocked):
    rows = {row["name"]: row for row in service.list_locations(stocked)}
    assert (rows["Binder 1"]["entries"], rows["Binder 1"]["copies"]) == (2, 7)
    assert (rows["Blue box"]["entries"], rows["Blue box"]["copies"]) == (1, 1)
    assert (rows["Dragons"]["entries"], rows["Dragons"]["copies"], rows["Dragons"]["value"]) == (
        0,
        0,
        None,
    )
    assert rows["Binder 1"]["value"] > 0


def test_copies_of_card_spans_printings(stocked):
    oracle = stocked.execute(
        "SELECT oracle_id FROM cat.printings WHERE name = 'Lightning Bolt' LIMIT 1"
    ).fetchone()[0]
    copies = service.copies_of_card(stocked, oracle)
    assert [(row["set_code"], row["quantity"], row["location_name"]) for row in copies] == [
        ("lea", 1, "Blue box"),
        ("m10", 3, "Binder 1"),
    ]


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
        box = service.create_location(conn, "Box")
        conn.execute(
            "INSERT INTO entries (scryfall_id, oracle_id, name, set_code, collector_number,"
            " quantity, location_id, created_at, updated_at)"
            " VALUES ('p', 'o', 'Lightning Bolt', 'm10', '146', 2, ?, 'now', 'now')",
            (box,),
        )
        rows, total = service.list_entries(conn)
        assert rows[0]["name"] == "Lightning Bolt" and rows[0]["value"] is None
        assert (total["copies"], total["value"]) == (2, None)
        assert service.list_locations(conn)[0]["copies"] == 2
        service.move_entry(conn, rows[0]["id"], 1, None)  # moving needs no catalog either
        assert service.list_entries(conn)[1]["entries"] == 2
