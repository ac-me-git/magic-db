"""Import and export (SPEC §8)."""

import csv
import io
import json
import shutil
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from magicdb import db
from magicdb.app import create_app
from magicdb.collection import exporters, importers, service, transfer
from magicdb.collection.importers import Defaults, FileProblem, profiles
from magicdb.collection.service import CollectionError
from magicdb.config import load_settings
from tests.conftest import FIXTURES

TOPDECKED = FIXTURES / "topdecked-sample.csv"


@pytest.fixture
def conn(catalog):
    with closing(db.connect(catalog)) as connection:
        yield connection


@pytest.fixture
def other(catalog, tmp_path):
    """A second, empty collection that sees the same catalog."""
    settings = load_settings({"MAGICDB_DATA_DIR": str(tmp_path / "other")})
    shutil.copytree(catalog.catalog_dir, settings.catalog_dir)
    db.migrate(settings)
    return settings


def pid(conn, name, set_code):
    return conn.execute(
        "SELECT id FROM cat.printings WHERE name = ? AND set_code = ?", (name, set_code)
    ).fetchone()[0]


def snapshot(conn):
    """Everything an export must carry over, in a comparable form."""
    entries = conn.execute(
        "SELECT e.scryfall_id, e.oracle_id, e.name, e.set_code, e.collector_number, e.quantity, "
        "e.finish, e.condition, e.language, l.name, e.notes, e.purchase_price, e.created_at "
        "FROM entries e LEFT JOIN locations l ON l.id = e.location_id"
    )
    places = conn.execute("SELECT name, kind, notes FROM locations ORDER BY sort_order, id")
    return sorted(tuple(row) for row in entries), [tuple(row) for row in places]


@pytest.fixture
def stocked(conn):
    binder = service.create_location(conn, "Binder 1", "binder", "top shelf")
    box = service.create_location(conn, "Blue box", "box")
    deck = service.create_location(conn, "Dragons, the deck", "deck")
    service.add_entry(conn, pid(conn, "Lightning Bolt", "m10"), 3, location_id=binder)
    service.add_entry(
        conn,
        pid(conn, "Lightning Bolt", "m10"),
        1,
        finish="foil",
        condition="EX",
        language="it",
        location_id=binder,
        notes='signed, "mint"',
        purchase_price="12,50",
    )
    service.add_entry(
        conn, pid(conn, "Jace, the Mind Sculptor", "wwk"), 1, condition="LP", location_id=box
    )
    service.add_entry(conn, pid(conn, "Shivan Dragon", "m20"), 2, condition="PO", location_id=deck)
    service.add_entry(conn, pid(conn, "Sol Ring", "c21"), 4, language="ja")
    conn.execute("UPDATE entries SET created_at = '2024-0' || id || '-01T10:00:00+00:00'")
    conn.commit()
    return conn


def bring_in(conn, data, defaults=None, **options):
    parsed = importers.parse_file(data if isinstance(data, bytes) else data.encode())
    importers.resolve(conn, parsed, defaults)
    result = service.import_entries(
        conn, importers.to_items(parsed), locations=parsed.locations, **options
    )
    return parsed, result


def table(text, defaults=None, conn=None):
    """Parse and resolve a small CSV given as text; return its rows."""
    parsed = importers.parse_file(text.encode())
    importers.resolve(conn, parsed, defaults)
    return parsed.rows


# --- export ---------------------------------------------------------------------------


def test_csv_export(stocked):
    rows = list(csv.reader(io.StringIO(exporters.export_csv(stocked))))
    assert tuple(rows[0]) == exporters.CSV_COLUMNS
    assert len(rows) == 6
    foil = next(r for r in rows if r[6] == "foil")
    assert foil == [
        "Lightning Bolt",
        "m10",
        "Magic 2010",
        "146",
        pid(stocked, "Lightning Bolt", "m10"),
        "1",
        "foil",
        "EX",
        "it",
        "Binder 1",
        "binder",
        'signed, "mint"',
        "12.5",
        "2024-02-01T10:00:00+00:00",
    ]
    # location by location in the owner's order, loose cards last
    assert [r[9] for r in rows[1:]] == ["Binder 1", "Binder 1", "Blue box", "Dragons, the deck", ""]


def test_json_export(stocked):
    service.create_location(stocked, "Empty shelf", "other", "for later")
    dump = json.loads(exporters.export_json(stocked))
    assert dump["magic_db_export"] == 1 and dump["schema_version"] == 1
    assert [loc["name"] for loc in dump["locations"]] == [
        "Binder 1",
        "Blue box",
        "Dragons, the deck",
        "Empty shelf",
    ]
    assert dump["locations"][0] == {"name": "Binder 1", "kind": "binder", "notes": "top shelf"}
    assert len(dump["entries"]) == 5
    ring = next(e for e in dump["entries"] if e["name"] == "Sol Ring")
    assert ring["location"] is None and ring["language"] == "ja" and ring["quantity"] == 4


def test_export_of_an_empty_collection(conn):
    assert exporters.export_csv(conn).strip() == ",".join(exporters.CSV_COLUMNS)
    assert json.loads(exporters.export_json(conn))["entries"] == []


def test_csv_round_trip(stocked, other):
    exported = exporters.export_csv(stocked)
    with closing(db.connect(other)) as fresh:
        parsed, result = bring_in(fresh, exported)
        assert parsed.profile == "native"
        assert importers.summary(parsed) == {
            "rows": 5,
            "matched": 5,
            "guessed": 0,
            "ambiguous": 0,
            "failed": 0,
            "copies": 11,
        }
        assert (result["entries"], result["copies"]) == (5, 11)
        entries, places = snapshot(fresh)
        assert entries == snapshot(stocked)[0]
        # a CSV knows the locations that hold cards, with their kind but not their notes
        assert [p[:2] for p in places] == [p[:2] for p in snapshot(stocked)[1]]
        assert exporters.export_csv(fresh) == exported  # and it exports identically


def test_json_round_trip_keeps_empty_locations_notes_and_order(stocked, other):
    service.create_location(stocked, "Empty shelf", "other", "for later")
    service.shift_location(stocked, 4, -1)
    with closing(db.connect(other)) as fresh:
        bring_in(fresh, exporters.export_json(stocked))
        assert snapshot(fresh) == snapshot(stocked)


def test_importing_twice_adds_up(stocked, other):
    exported = exporters.export_csv(stocked)
    with closing(db.connect(other)) as fresh:
        bring_in(fresh, exported)
        bring_in(fresh, exported)
        assert fresh.execute("SELECT COUNT(*), SUM(quantity) FROM entries").fetchone()[:] == (5, 22)
        bring_in(fresh, exported, replace=True)
        assert snapshot(fresh)[0] == snapshot(stocked)[0]


# --- finding the printing -------------------------------------------------------------

HEAD = "Name,Set code,Set name,Collector number,Scryfall ID,Quantity\n"


def test_resolution_by_scryfall_id_wins_over_everything(conn):
    bolt = pid(conn, "Lightning Bolt", "lea")
    (row,) = table(HEAD + f"Totally Wrong,xxx,Nowhere,999,{bolt},2\n", conn=conn)
    assert row.status == "matched" and not row.guessed
    assert (row.card["name"], row.card["set_code"], row.fields["quantity"]) == (
        "Lightning Bolt",
        "lea",
        2,
    )
    upper = table(HEAD + f"x,,,,{bolt.upper()},1\n", conn=conn)[0]
    assert upper.card["scryfall_id"] == bolt


def test_resolution_by_set_and_number(conn):
    (row,) = table(HEAD + "Lightning Bolt,M10,,146,,1\n", conn=conn)
    assert row.status == "matched" and row.card["scryfall_id"] == pid(conn, "Lightning Bolt", "m10")
    (nameless,) = table(HEAD + ",m10,,146,,1\n", conn=conn)
    assert nameless.card["name"] == "Lightning Bolt"
    # an id that is not in the catalog is ignored, the rest of the row still works
    stale = "00000000-0000-4000-8000-000000000000"
    assert table(HEAD + f"Lightning Bolt,m10,,146,{stale},1\n", conn=conn)[0].status == "matched"


def test_number_that_points_to_another_card_is_not_trusted(conn):
    # number 146 of M10 is Lightning Bolt: the name and the set decide
    (row,) = table(HEAD + "Baneslayer Angel,m10,,146,,1\n", conn=conn)
    assert row.status == "matched" and row.card["name"] == "Baneslayer Angel"


def test_resolution_by_name_and_set(conn):
    (by_code,) = table(HEAD + "lightning bolt,LEA,,,,1\n", conn=conn)
    assert by_code.card["set_code"] == "lea" and not by_code.guessed
    (by_name,) = table(HEAD + "Lightning Bolt,,limited edition alpha,,,1\n", conn=conn)
    assert by_name.card["set_code"] == "lea"
    (face,) = table(HEAD + "Delver of Secrets,isd,,,,1\n", conn=conn)
    assert face.card["name"] == "Delver of Secrets // Insectile Aberration"


def test_several_versions_in_one_set_is_a_guess(conn):
    twice = conn.execute(
        "SELECT name FROM cat.printings WHERE set_code = 'arn' GROUP BY name HAVING COUNT(*) > 1"
    ).fetchone()[0]
    (row,) = table(HEAD + f'"{twice}",arn,,,,1\n', conn=conn)
    assert row.status == "matched" and row.guessed
    best = conn.execute(
        "SELECT id, MAX(pref) FROM cat.printings WHERE name = ? AND set_code = 'arn'", (twice,)
    ).fetchone()[0]
    assert row.card["scryfall_id"] == best


def test_resolution_by_name_alone(conn):
    (single,) = table(HEAD + "Sol Ring,,,,,1\n", conn=conn)  # one printing in the catalog
    assert single.status == "matched" and single.card["set_code"] == "c21"
    (many,) = table(HEAD + "Lightning Bolt,,,,,1\n", conn=conn)
    assert (many.status, many.problem, many.detail) == ("ambiguous", "ambiguous", {"printings": 6})
    (wrong_set,) = table(HEAD + "Lightning Bolt,zzz,,,,1\n", conn=conn)
    assert wrong_set.status == "ambiguous"


def test_unknown_cards_fail(conn):
    rows = table(HEAD + "Not A Real Card,,,,,1\n,,,,,1\n,zzz,,7,,1\n", conn=conn)
    assert [(r.status, r.problem) for r in rows] == [("failed", "unknown_card")] * 3
    digital = conn.execute("SELECT name FROM cat.printings WHERE is_digital = 1").fetchone()[0]
    assert table(HEAD + f'"{digital}",,,,,1\n', conn=conn)[0].problem in ("unknown_card", "")


# --- values ---------------------------------------------------------------------------

BOLT = "Lightning Bolt,m10,146"


def one(conn, header, line, defaults=None):
    (row,) = table(f"Name,Set code,Collector number,{header}\n{BOLT},{line}\n", defaults, conn)
    return row


@pytest.mark.parametrize("quantity", ["0", "-1", "1.5", "many", "10000"])
def test_bad_quantity_fails(conn, quantity):
    row = one(conn, "Quantity", quantity)
    assert (row.status, row.problem) == ("failed", "bad_quantity")


def test_quantity_defaults_to_one_when_there_is_no_column(conn):
    assert one(conn, "Quantity", "").fields["quantity"] == 1  # an empty cell too
    (row,) = table(f"Name,Set code,Collector number\n{BOLT}\n", conn=conn)
    assert row.status == "matched" and row.fields["quantity"] == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", "nonfoil"),
        ("nonfoil", "nonfoil"),
        ("normal", "nonfoil"),
        ("Non-Foil", "nonfoil"),
        ("foil", "foil"),
        ("FOIL", "foil"),
        ("etched", "etched"),
        ("foil_etched", "etched"),
        ("holo", None),
    ],
)
def test_finish_values(conn, value, expected):
    row = one(conn, "Finish", value)
    if expected:
        assert row.fields["finish"] == expected
    else:
        assert (row.problem, row.detail) == ("bad_finish", {"value": "holo"})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("MT", "MT"),
        ("nm", "NM"),
        ("EX", "EX"),
        ("Good", "GD"),
        ("LP", "LP"),
        ("Light Played", "LP"),
        ("pl", "PL"),
        ("Poor", "PO"),
    ],
)
def test_cardmarket_conditions_are_kept(conn, value, expected):
    assert one(conn, "Condition", value).fields["condition"] == expected


US = "Count,Name,Edition,Collector Number,Condition\n1,Lightning Bolt,m10,146,{}\n"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Mint", "MT"),
        ("Near Mint", "NM"),
        ("NM", "NM"),
        ("Lightly Played", "EX"),
        ("LP", "EX"),
        ("lightly_played", "EX"),
        ("Moderately Played", "GD"),
        ("MP", "GD"),
        ("Heavily Played", "PL"),
        ("HP", "PL"),
        ("Damaged", "PO"),
        ("DMG", "PO"),
    ],
)
def test_us_conditions_are_translated(conn, value, expected):
    (row,) = table(US.format(value), conn=conn)
    assert row.status == "matched" and row.fields["condition"] == expected


def test_the_same_code_means_different_things_in_the_two_scales(conn):
    assert one(conn, "Condition", "LP").fields["condition"] == "LP"  # Cardmarket: Light Played
    assert table(US.format("LP"), conn=conn)[0].fields["condition"] == "EX"  # US: Lightly Played


def test_unknown_condition_fails_and_blank_uses_the_default(conn):
    row = one(conn, "Condition", "Pristine")
    assert (row.status, row.problem, row.detail) == (
        "failed",
        "bad_condition",
        {"value": "Pristine"},
    )
    assert table(US.format("EX"), conn=conn)[0].problem == "bad_condition"  # not a US grade
    assert one(conn, "Condition", "").fields["condition"] == "NM"
    assert one(conn, "Condition", "", Defaults(condition="GD")).fields["condition"] == "GD"
    assert one(conn, "Condition", "PL", Defaults(condition="GD")).fields["condition"] == "PL"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("en", "en"),
        ("English", "en"),
        ("IT", "it"),
        ("Italian", "it"),
        ("jp", "ja"),
        ("Japanese", "ja"),
        ("Chinese Simplified", "zhs"),
        ("zh-TW", "zht"),
        ("Klingon", None),
    ],
)
def test_languages(conn, value, expected):
    row = one(conn, "Language", value)
    if expected:
        assert row.fields["language"] == expected
    else:
        assert row.problem == "bad_language"
    assert one(conn, "Language", "", Defaults(language="it")).fields["language"] == "it"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("", None), ("2.5", 2.5), ('"1,50"', 1.5), ("€3", 3.0), ("$ 4.20", 4.2), ("0", 0.0)],
)
def test_prices(conn, value, expected):
    assert one(conn, "Purchase price", value).fields["purchase_price"] == expected


def test_bad_price_fails(conn):
    assert one(conn, "Purchase price", "cheap").problem == "bad_price"
    assert one(conn, "Purchase price", "-3").problem == "bad_price"


def test_dates_locations_and_notes(conn):
    row = one(
        conn,
        "Added,Location,Location kind,Notes",
        "2025-02-11T15:31:46.501Z,  Blue   box ,BOX, signed ",
    )
    assert row.fields["added_at"] == "2025-02-11T15:31:46+00:00"
    assert (row.fields["location"], row.fields["location_kind"]) == ("Blue box", "box")
    assert row.fields["notes"] == "signed"
    assert one(conn, "Added", "yesterday").fields["added_at"] is None  # unreadable: ignored
    assert (
        one(conn, "Added", "2024-03-01 08:00:00").fields["added_at"] == "2024-03-01T08:00:00+00:00"
    )

    fallback = one(conn, "Location", "", Defaults(location="Binder 9", location_kind="binder"))
    assert (fallback.fields["location"], fallback.fields["location_kind"]) == ("Binder 9", "binder")
    own = one(conn, "Location", "Mine", Defaults(location="Binder 9", location_kind="binder"))
    assert (own.fields["location"], own.fields["location_kind"]) == ("Mine", "")


# --- formats --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (",".join(exporters.CSV_COLUMNS), "native"),
        (
            "QUANTITY,NAME,SETCODE,SETNAME,COLLECTOR NUMBER,FINISH,PRICE,RARITY,ID,ACQUIRED DATE",
            "topdecked",
        ),
        (
            "Count,Tradelist Count,Name,Edition,Condition,Language,Foil,Tags,Last Modified,Collector Number",
            "moxfield",
        ),
        (
            "Name,Set code,Set name,Collector number,Foil,Rarity,Quantity,ManaBox ID,Scryfall ID",
            "manabox",
        ),
        ("Binder Name,Binder Type,Name,Set code,Foil,Quantity,ManaBox ID,Scryfall ID", "manabox"),
        ("name,qty", "native"),
        ("Card,Quantity", None),
    ],
)
def test_format_detection(header, expected):
    profile = profiles.detect(header.split(","))
    assert (profile.name if profile else None) == expected


def test_moxfield_export(conn):
    text = (
        '"Count","Tradelist Count","Name","Edition","Condition","Language","Foil","Tags",'
        '"Last Modified","Collector Number","Alter","Proxy","Purchase Price"\n'
        '"2","0","Lightning Bolt","m10","Near Mint","English","foil","","2024-05-01 10:00:00.000000","146","False","False","1.50"\n'
        '"1","0","Jace, the Mind Sculptor","wwk","Lightly Played","Italian","","","2024-05-02 10:00:00.000000","31","False","False",""\n'
    )
    parsed = importers.parse_file(text.encode())
    importers.resolve(conn, parsed)
    assert (parsed.profile, parsed.scale) == ("moxfield", "us")
    first, second = parsed.rows
    assert first.fields == {
        "quantity": 2,
        "finish": "foil",
        "condition": "NM",
        "language": "en",
        "location": "",
        "location_kind": "other",
        "notes": None,
        "purchase_price": 1.5,
        "added_at": "2024-05-01T10:00:00+00:00",
    }
    assert first.card["set_code"] == "m10"
    assert (second.fields["condition"], second.fields["language"], second.fields["finish"]) == (
        "EX",
        "it",
        "nonfoil",
    )
    assert second.card["name"] == "Jace, the Mind Sculptor"


def test_manabox_export(conn):
    bolt = pid(conn, "Lightning Bolt", "m10")
    text = (
        "Binder Name,Binder Type,Name,Set code,Set name,Collector number,Foil,Rarity,Quantity,"
        "ManaBox ID,Scryfall ID,Purchase price,Misprint,Altered,Condition,Language,Purchase price currency\n"
        f"Red deck,deck,Lightning Bolt,M10,Magic 2010,146,normal,common,4,123,{bolt},0.5,false,false,near_mint,en,EUR\n"
        f"Trades,binder,Lightning Bolt,M10,Magic 2010,146,etched,common,1,124,{bolt},,false,false,heavily_played,ja,EUR\n"
    )
    parsed = importers.parse_file(text.encode())
    importers.resolve(conn, parsed)
    assert (parsed.profile, parsed.scale) == ("manabox", "us")
    first, second = parsed.rows
    assert (first.fields["location"], first.fields["location_kind"]) == ("Red deck", "deck")
    assert (
        first.fields["quantity"],
        first.fields["condition"],
        first.fields["purchase_price"],
    ) == (4, "NM", 0.5)
    assert (second.fields["finish"], second.fields["condition"], second.fields["language"]) == (
        "etched",
        "PL",
        "ja",
    )


def test_topdecked_sample_has_the_shape_of_the_real_export():
    lines = TOPDECKED.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith('QUANTITY,"NAME",SETCODE,"SETNAME","COLLECTOR NUMBER",FINISH,PRICE,')
    rows = list(csv.reader(lines))
    assert len(rows[0]) == 18 and len(rows) - 1 == 30
    assert {len(row) for row in rows[1:]} == {11}  # the split price, no trailing columns
    assert all(row[6].startswith("€") and len(row[7]) == 2 for row in rows[1:])


def test_topdecked_import(conn):
    parsed = importers.parse_file(TOPDECKED.read_bytes())
    importers.resolve(conn, parsed, Defaults(location="BlueBinders", location_kind="binder"))
    assert (parsed.profile, parsed.scale) == ("topdecked", "us")
    assert importers.summary(parsed) == {
        "rows": 30,
        "matched": 30,
        "guessed": 0,
        "ambiguous": 0,
        "failed": 0,
        "copies": 30,
    }
    first = parsed.rows[0]
    assert first.card["scryfall_id"] == pid(conn, "Lightning Bolt", "m10")
    assert first.fields == {
        "quantity": 1,
        "finish": "nonfoil",
        "condition": "NM",
        "language": "en",
        "location": "BlueBinders",
        "location_kind": "binder",
        "notes": None,
        "purchase_price": None,
        "added_at": "2025-02-11T15:31:46+00:00",
    }
    assert parsed.rows[6].card["name"] == "Jace, the Mind Sculptor"  # a quoted comma

    result = service.import_entries(conn, importers.to_items(parsed), source="topdecked: sample")
    assert (result["entries"], result["copies"], result["new_locations"]) == (
        30,
        30,
        ["BlueBinders"],
    )
    # one row per copy in the file becomes one entry per printing and finish
    bolts = conn.execute(
        "SELECT finish, quantity FROM entries WHERE name = 'Lightning Bolt' AND set_code = 'm10' "
        "ORDER BY finish"
    ).fetchall()
    assert [tuple(row) for row in bolts] == [("foil", 1), ("nonfoil", 2)]
    assert conn.execute("SELECT COUNT(*), SUM(quantity) FROM entries").fetchone()[:] == (29, 30)
    assert (
        conn.execute("SELECT MIN(created_at) FROM entries").fetchone()[0].startswith("2025-02-11")
    )


def test_topdecked_rows_with_more_columns_filled(conn):
    """Beyond what the real sample shows: the optional columns, read in header order."""
    bolt = pid(conn, "Lightning Bolt", "m10")
    header = TOPDECKED.read_text(encoding="utf-8").splitlines()[0]
    rows = [
        f'2,"Lightning Bolt",m10,"Magic 2010",146,foil,€1,20,common,{bolt},2025-01-05T09:00:00.000Z,€0,80,it,,,,LP,"signed, front"',
        f'1,"Lightning Bolt",m10,"Magic 2010",146,nonfoil,€12,common,{bolt},2025-01-05T09:00:00.000Z',
        '1,"Lightning Bolt",m10,"Magic 2010",146,nonfoil,€0,15,common',
        '1,"Lightning Bolt"',
    ]
    parsed = importers.parse_file((header + "\n" + "\n".join(rows) + "\n").encode())
    importers.resolve(conn, parsed)
    full, whole_price, no_id, short = parsed.rows
    assert full.fields == {
        "quantity": 2,
        "finish": "foil",
        "condition": "EX",
        "language": "it",
        "location": "",
        "location_kind": "other",
        "notes": "signed, front",
        "purchase_price": 0.8,
        "added_at": "2025-01-05T09:00:00+00:00",
    }
    assert whole_price.status == "matched" and whole_price.fields["added_at"]
    assert no_id.status == "matched" and no_id.card["scryfall_id"] == bolt  # set + number
    assert (short.status, short.problem) == ("ambiguous", "ambiguous")


def test_rejoin_prices():
    assert profiles.rejoin_prices(["€0", "15", "x"]) == ["€0.15", "x"]
    assert profiles.rejoin_prices(["2025-01-01", "€12", "05", "it", "", "€3", "5"]) == [
        "2025-01-01",
        "€12.05",
        "it",
        "",
        "€3.5",
    ]
    assert profiles.rejoin_prices(["€12", "it"]) == ["€12", "it"]
    assert profiles.rejoin_prices(["15", "20"]) == ["15", "20"]
    assert profiles.rejoin_prices([]) == []


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"", "empty"),
        (b"   \n\n", "empty"),
        (b"Name,Set code\n", "empty"),
        (b"Card,Qty\nLightning Bolt,1\n", "unknown_format"),
        (b"\x00\x01\x02\xff\xfe binary \x00\n\x03\x04\n", "unknown_format"),
        (b'{"hello": "world"}', "unknown_format"),
        (b'{"magic_db_export": 1, "entries": [', "unreadable"),
    ],
)
def test_files_that_cannot_be_imported(data, expected):
    with pytest.raises(FileProblem) as excinfo:
        importers.parse_file(data)
    assert excinfo.value.code == expected


def test_file_encodings_and_delimiters(conn):
    plain = "Name;Set code;Collector number;Notes\nLightning Bolt;m10;146;più bella\n"
    for data in (plain.encode("utf-8"), plain.encode("utf-8-sig"), plain.encode("cp1252")):
        parsed = importers.parse_file(data)
        importers.resolve(conn, parsed)
        assert parsed.rows[0].status == "matched" and parsed.rows[0].fields["notes"] == "più bella"
    tabs = importers.parse_file(b"Name\tSet code\tCollector number\nLightning Bolt\tm10\t146\n")
    assert tabs.rows[0].collector_number == "146"
    spaced = importers.parse_file(b"Name,Set code\n\n\nLightning Bolt,m10\n,\n")
    assert len(spaced.rows) == 1 and spaced.rows[0].line == 2


def test_resolve_needs_the_catalog(settings):
    db.migrate(settings)
    with closing(db.connect(settings)) as conn, pytest.raises(FileProblem) as excinfo:
        importers.resolve(conn, importers.parse_file(b"Name\nLightning Bolt\n"))
    assert excinfo.value.code == "no_catalog"


# --- writing --------------------------------------------------------------------------


def test_import_is_one_transaction_with_one_history_row(conn):
    parsed = importers.parse_file(TOPDECKED.read_bytes())
    importers.resolve(conn, parsed)
    items = importers.to_items(parsed)
    service.import_entries(conn, items, source="topdecked: x.csv")
    log = conn.execute("SELECT action, detail FROM history").fetchall()
    assert [row["action"] for row in log] == ["import"]
    assert json.loads(log[0]["detail"]) == {
        "source": "topdecked: x.csv",
        "replace": False,
        "entries": 30,
        "copies": 30,
        "new_locations": [],
    }

    broken = [dict(items[0]), {**items[1], "finish": "shiny"}]
    before = conn.execute("SELECT COUNT(*), SUM(quantity) FROM entries").fetchone()[:]
    with pytest.raises(CollectionError):
        service.import_entries(conn, broken, replace=True)
    assert conn.execute("SELECT COUNT(*), SUM(quantity) FROM entries").fetchone()[:] == before
    assert conn.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 1


def test_import_reuses_locations_whatever_the_case(conn):
    service.create_location(conn, "Blue Box", "box")
    text = "Name,Set code,Collector number,Location,Location kind\n"
    text += "Lightning Bolt,m10,146,blue box,deck\nSol Ring,c21,263,New Shelf,shelf\n"
    _, result = bring_in(conn, text)
    assert result["new_locations"] == ["New Shelf"]
    places = [tuple(r) for r in conn.execute("SELECT name, kind FROM locations ORDER BY id")]
    assert places == [("Blue Box", "box"), ("New Shelf", "other")]  # unknown kind -> other


def test_import_keeps_the_earliest_date_when_rows_merge(conn):
    text = "Name,Set code,Collector number,Added\n"
    text += (
        "Lightning Bolt,m10,146,2023-06-01T00:00:00Z\nLightning Bolt,m10,146,2021-01-01T00:00:00Z\n"
    )
    bring_in(conn, text)
    row = conn.execute("SELECT quantity, created_at FROM entries").fetchone()
    assert tuple(row) == (2, "2021-01-01T00:00:00+00:00")


# --- pages ----------------------------------------------------------------------------


@pytest.fixture
def client(catalog):
    return TestClient(create_app(catalog), follow_redirects=False)


def upload(client, data, name="collection.csv"):
    payload = data if isinstance(data, bytes) else data.encode()
    return client.post("/import", files={"file": (name, payload, "text/csv")})


def token_of(page):
    return page.text.split('action="/import/')[1].split("/confirm")[0]


def counts(settings):
    with closing(db.connect(settings)) as conn:
        entries = conn.execute("SELECT COUNT(*), IFNULL(SUM(quantity), 0) FROM entries").fetchone()
        return (*entries, conn.execute("SELECT COUNT(*) FROM history").fetchone()[0])


def backups(settings):
    return (
        sorted(p.name for p in settings.backup_dir.glob("*.db"))
        if settings.backup_dir.exists()
        else []
    )


def test_import_and_export_pages(client):
    assert 'enctype="multipart/form-data"' in client.get("/import").text
    page = client.get("/export").text
    assert 'href="/export/csv"' in page and 'href="/export/json"' in page
    collection = client.get("/collection").text
    assert 'href="/import"' in collection and 'href="/export"' in collection


def test_downloads(client):
    csv_file = client.get("/export/csv")
    assert csv_file.headers["content-type"] == "text/csv; charset=utf-8"
    assert 'attachment; filename="magic-db-collection-' in csv_file.headers["content-disposition"]
    assert csv_file.content.startswith(b"\xef\xbb\xbfName,Set code,")  # opens right in Excel
    json_file = client.get("/export/json")
    assert json_file.headers["content-type"] == "application/json; charset=utf-8"
    assert json_file.json()["entries"] == []


def test_preview_writes_nothing(client, catalog):
    page = upload(
        client,
        TOPDECKED.read_bytes(),
        "binder-BlueBinders-00000000-0000-4000-8000-000000000000.csv",
    )
    assert page.status_code == 200
    assert "Nothing has been written yet." in page.text
    assert "TopDecked" in page.text and "<strong>30</strong>" in page.text
    assert "Location for the 30 rows that do not name one" in page.text
    # the binder's name is offered as a new location
    assert 'name="new_location" value="BlueBinders"' in page.text
    assert '<option value="new" selected>' in page.text
    assert "Import 30 rows" in page.text and ">Lightning Bolt</a>" in page.text
    assert counts(catalog) == (0, 0, 0) and backups(catalog) == []


def test_confirm_backs_up_first_then_writes(client, catalog):
    add = client.post("/add", data={"printing": _first_id(catalog), "quantity": "2"})
    assert add.status_code == 303
    page = upload(
        client,
        TOPDECKED.read_bytes(),
        "binder-BlueBinders-00000000-0000-4000-8000-000000000000.csv",
    )
    done = client.post(
        f"/import/{token_of(page)}/confirm",
        data={
            "filename": "x.csv",
            "location": "new",
            "new_location": "BlueBinders",
            "new_kind": "binder",
            "condition": "EX",
            "language": "it",
            "mode": "add",
        },
    )
    assert (
        done.status_code == 303
        and "/done?entries=30&copies=30&skipped=0" in done.headers["location"]
    )
    assert "30 cards imported, in 30 rows." in client.get(done.headers["location"]).text

    (backup,) = backups(catalog)
    assert backup.startswith("pre-import-")
    with closing(db.connect(catalog)) as conn:
        assert conn.execute("SELECT SUM(quantity) FROM entries").fetchone()[0] == 32
        imported = conn.execute(
            "SELECT DISTINCT e.condition, e.language, l.name, l.kind FROM entries e "
            "JOIN locations l ON l.id = e.location_id"
        ).fetchall()
        assert [tuple(row) for row in imported] == [("EX", "it", "BlueBinders", "binder")]
        assert [r[0] for r in conn.execute("SELECT action FROM history")] == ["add", "import"]
    # the backup holds the collection as it was before the import
    import sqlite3

    with closing(sqlite3.connect(catalog.backup_dir / backup)) as before:
        assert before.execute("SELECT SUM(quantity) FROM entries").fetchone()[0] == 2


def _first_id(settings):
    with closing(db.connect(settings)) as conn:
        return pid(conn, "Sol Ring", "c21")


def test_replace_mode_and_existing_location(client, catalog):
    client.post("/locations", data={"name": "Shelf", "kind": "other"})
    client.post("/add", data={"printing": _first_id(catalog), "quantity": "5"})
    page = upload(client, TOPDECKED.read_bytes())
    assert "Remove them first (1 entries)" in page.text
    assert '<option value="new" selected>' not in page.text  # no binder name in this file name
    client.post(f"/import/{token_of(page)}/confirm", data={"location": "1", "mode": "replace"})
    with closing(db.connect(catalog)) as conn:
        assert conn.execute("SELECT SUM(quantity) FROM entries").fetchone()[0] == 30
        assert conn.execute("SELECT DISTINCT location_id FROM entries").fetchall()[0][0] == 1


def test_problem_rows_are_listed_and_downloadable(client, catalog):
    text = (
        "Name,Set code,Collector number,Quantity,Condition\n"
        "Lightning Bolt,m10,146,2,NM\n"
        "Lightning Bolt,,,1,NM\n"
        "Not A Card,,,1,NM\n"
        "Sol Ring,c21,263,three,NM\n"
        "Sol Ring,c21,263,1,Pristine\n"
    )
    page = upload(client, text)
    assert "Rows that will not be imported (4)" in page.text
    for message in [
        "6 printings have this name and the file does not say which.",
        "No such card in the catalog.",
        "The quantity is not a whole number from 1 to 9999.",
        "Unknown condition: Pristine",
    ]:
        assert message in page.text
    assert "Import 1 rows" in page.text
    token = token_of(page)

    unresolved = client.get(f"/import/{token}/unresolved.csv")
    assert unresolved.headers["content-type"] == "text/csv; charset=utf-8"
    rows = list(csv.reader(io.StringIO(unresolved.content.decode("utf-8-sig"))))
    assert rows[0] == [
        "Name",
        "Set code",
        "Collector number",
        "Quantity",
        "Condition",
        "Line",
        "Problem",
    ]
    assert [row[5] for row in rows[1:]] == ["3", "4", "5", "6"]
    assert rows[2][:2] == ["Not A Card", ""] and rows[2][6] == "No such card in the catalog."

    done = client.post(f"/import/{token}/confirm", data={})
    assert "entries=1&copies=2&skipped=4" in done.headers["location"]
    result = client.get(done.headers["location"]).text
    assert "4 rows were left out." in result and f"/import/{token}/unresolved.csv" in result
    assert counts(catalog)[:2] == (1, 2)


def test_nothing_importable(client):
    page = upload(client, "Name\nNot A Card\n")
    assert "No row of this file can be imported." in page.text
    assert "/confirm" not in page.text


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"", "The file is empty."),
        (b"Card,Qty\nx,1\n", "not in a format the import understands"),
        (b'{"magic_db_export": 1, "entries": [', "cannot be read as CSV or JSON"),
    ],
)
def test_bad_uploads_are_explained(client, catalog, data, message):
    page = upload(client, data)
    assert page.status_code == 400 and message in page.text
    assert not list(catalog.tmp_dir.glob("import-*")) if catalog.tmp_dir.exists() else True


def test_upload_too_big(client, monkeypatch):
    monkeypatch.setattr(transfer, "MAX_UPLOAD", 100)
    page = upload(client, "Name\n" + "Lightning Bolt\n" * 50)
    assert page.status_code == 400 and "The file is too big." in page.text


def test_confirm_with_a_bad_choice_shows_the_preview_again(client, catalog):
    page = upload(client, TOPDECKED.read_bytes())
    token = token_of(page)
    for data in ({"location": "new", "new_location": "  "}, {"location": "99"}, {"location": "x"}):
        again = client.post(f"/import/{token}/confirm", data=data)
        assert again.status_code == 400 and "Import 30 rows" in again.text
    assert (
        "A location needs a name"
        in client.post(
            f"/import/{token}/confirm", data={"location": "new", "new_location": ""}
        ).text
    )
    assert counts(catalog) == (0, 0, 0) and backups(catalog) == []


@pytest.mark.parametrize("token", ["0" * 32, "nonsense", "..%2F..%2Fcollection"])
def test_unknown_upload(client, token):
    gone = client.post(f"/import/{token}/confirm", data={})
    assert gone.status_code in (303, 404)
    if gone.status_code == 303:
        assert gone.headers["location"] == "/import?error=expired"
        assert "no longer here" in client.get(gone.headers["location"]).text
    assert client.get(f"/import/{token}/unresolved.csv").status_code == 404


def test_old_uploads_are_forgotten(client, catalog):
    import os
    import time

    token_of(upload(client, TOPDECKED.read_bytes()))
    (old,) = catalog.tmp_dir.glob("import-*.upload")
    past = time.time() - transfer.KEEP_SECONDS - 60
    os.utime(old, (past, past))
    upload(client, TOPDECKED.read_bytes())
    assert old not in list(catalog.tmp_dir.glob("import-*.upload"))
    assert len(list(catalog.tmp_dir.glob("import-*.upload"))) == 1


def test_round_trip_through_the_pages(client, catalog, other):
    client.post("/locations", data={"name": "Binder 1", "kind": "binder"})
    client.post(
        "/add",
        data={
            "printing": _first_id(catalog),
            "quantity": "3",
            "location": "1",
            "finish": "foil",
            "condition": "GD",
            "language": "de",
        },
    )
    exported = client.get("/export/csv").content

    fresh = TestClient(create_app(other), follow_redirects=False)
    page = upload(fresh, exported, "magic-db-collection.csv")
    assert "magic-db" in page.text and "Location for the 0 rows" in page.text
    fresh.post(f"/import/{token_of(page)}/confirm", data={})
    assert fresh.get("/export/csv").content == exported


def test_import_pages_in_italian(client):
    client.get("/lang/it")
    assert "Formati riconosciuti" in client.get("/import").text
    page = upload(client, "Name\nLightning Bolt\n")
    assert "6 stampe hanno questo nome" in page.text
    assert "Non è ancora stato scritto niente." in page.text
    assert "Il file è vuoto." in upload(client, b"").text


def test_import_needs_the_catalog(settings):
    client = TestClient(create_app(settings))
    assert client.get("/import").status_code == 503
    assert upload(client, "Name\nx\n").status_code == 400
    assert client.get("/export").status_code == 200  # exporting works without it
