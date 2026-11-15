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
from magicdb.collection.importers import FileProblem, profiles
from magicdb.config import load_settings
from tests.conftest import FIXTURES, editing

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
        "SELECT scryfall_id, oracle_id, name, set_code, collector_number, quantity, finish, "
        "notes, purchase_price, created_at FROM entries"
    )
    return sorted(tuple(row) for row in entries)


@pytest.fixture
def stocked(conn):
    service.add_entry(conn, pid(conn, "Lightning Bolt", "m10"), 3)
    service.add_entry(
        conn,
        pid(conn, "Lightning Bolt", "m10"),
        1,
        finish="foil",
        notes='signed, "mint"',
        purchase_price="12,50",
    )
    service.add_entry(conn, pid(conn, "Jace, the Mind Sculptor", "wwk"), 1)
    service.add_entry(conn, pid(conn, "Shivan Dragon", "m20"), 2)
    service.add_entry(conn, pid(conn, "Sol Ring", "c21"), 4)
    conn.execute("UPDATE entries SET created_at = '2024-0' || id || '-01T10:00:00+00:00'")
    conn.commit()
    return conn


def bring_in(conn, data, **options):
    parsed = importers.parse_file(data if isinstance(data, bytes) else data.encode())
    importers.resolve(conn, parsed)
    result = service.import_entries(conn, importers.to_items(parsed), **options)
    return parsed, result


def table(text, conn=None):
    """Parse and resolve a small CSV given as text; return its rows."""
    parsed = importers.parse_file(text.encode())
    importers.resolve(conn, parsed)
    return parsed.rows


# --- export ---------------------------------------------------------------------------


def test_csv_export(stocked):
    rows = list(csv.reader(io.StringIO(exporters.export_csv(stocked))))
    assert tuple(rows[0]) == exporters.CSV_COLUMNS
    assert not {"Condition", "Language", "Location"} & set(rows[0])
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
        'signed, "mint"',
        "12.5",
        "2024-02-01T10:00:00+00:00",
    ]
    # by name
    assert [r[0] for r in rows[1:]] == [
        "Jace, the Mind Sculptor",
        "Lightning Bolt",
        "Lightning Bolt",
        "Shivan Dragon",
        "Sol Ring",
    ]


def test_json_export(stocked):
    dump = json.loads(exporters.export_json(stocked))
    assert dump["magic_db_export"] == 2 and dump["schema_version"] >= 4
    assert "locations" not in dump
    assert len(dump["entries"]) == 5
    ring = next(e for e in dump["entries"] if e["name"] == "Sol Ring")
    assert ring["quantity"] == 4 and ring["finish"] == "nonfoil"
    assert not {"condition", "language", "location"} & set(ring)


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
        assert snapshot(fresh) == snapshot(stocked)
        assert exporters.export_csv(fresh) == exported  # and it exports identically


def test_json_round_trip(stocked, other):
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
        assert snapshot(fresh) == snapshot(stocked)


OLD_CSV = (
    "Name,Set code,Set name,Collector number,Scryfall ID,Quantity,Finish,Condition,Language,"
    "Location,Location kind,Notes,Purchase price,Added\n"
    "Lightning Bolt,m10,Magic 2010,146,{bolt},2,nonfoil,EX,it,Binder 1,binder,,,"
    "2024-01-01T10:00:00+00:00\n"
    "Lightning Bolt,m10,Magic 2010,146,{bolt},3,nonfoil,NM,en,Blue box,box,,,"
    "2023-01-01T10:00:00+00:00\n"
)


def test_an_export_from_before_still_imports_and_its_copies_merge(conn):
    """A CSV written when copies had a condition, a language and a location: those
    columns are not read, and the two rows become one."""
    bolt = pid(conn, "Lightning Bolt", "m10")
    parsed, result = bring_in(conn, OLD_CSV.format(bolt=bolt))
    assert parsed.profile == "native" and importers.summary(parsed)["matched"] == 2
    rows = conn.execute("SELECT quantity, finish, created_at FROM entries").fetchall()
    assert [tuple(row) for row in rows] == [(5, "nonfoil", "2023-01-01T10:00:00+00:00")]


def test_a_json_export_from_before_still_imports(conn):
    bolt = pid(conn, "Lightning Bolt", "m10")
    old = {
        "magic_db_export": 1,
        "locations": [{"name": "Binder 1", "kind": "binder", "notes": None}],
        "decks": [],
        "entries": [
            {
                "name": "Lightning Bolt",
                "set_code": "m10",
                "collector_number": "146",
                "scryfall_id": bolt,
                "quantity": 2,
                "finish": "foil",
                "condition": "EX",
                "language": "it",
                "location": "Binder 1",
            },
        ],
    }
    bring_in(conn, json.dumps(old))
    assert conn.execute("SELECT quantity, finish FROM entries").fetchone()[:] == (2, "foil")


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


def one(conn, header, line):
    (row,) = table(f"Name,Set code,Collector number,{header}\n{BOLT},{line}\n", conn)
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


US = "Count,Name,Edition,Collector Number,Condition,Language\n1,Lightning Bolt,m10,146,{},{}\n"


@pytest.mark.parametrize(
    ("header", "value"),
    [
        ("Condition", "EX"),
        ("Condition", "Pristine"),
        ("Language", "it"),
        ("Language", "Klingon"),
        ("Location", "Blue box"),
    ],
)
def test_condition_language_and_location_columns_are_not_read(conn, header, value):
    """The collection keeps a printing, a finish and a quantity: what other tools write
    about condition, language or place is left out, and never makes a row fail."""
    row = one(conn, header, value)
    assert row.status == "matched"
    assert set(row.fields) == {"quantity", "finish", "notes", "purchase_price", "added_at"}
    (us,) = table(US.format("Heavily Played", "Japanese"), conn=conn)
    assert us.status == "matched" and us.fields["quantity"] == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [("", None), ("2.5", 2.5), ('"1,50"', 1.5), ("€3", 3.0), ("$ 4.20", 4.2), ("0", 0.0)],
)
def test_prices(conn, value, expected):
    assert one(conn, "Purchase price", value).fields["purchase_price"] == expected


def test_bad_price_fails(conn):
    assert one(conn, "Purchase price", "cheap").problem == "bad_price"
    assert one(conn, "Purchase price", "-3").problem == "bad_price"


def test_dates_and_notes(conn):
    row = one(conn, "Added,Notes", "2025-02-11T15:31:46.501Z, signed ")
    assert row.fields["added_at"] == "2025-02-11T15:31:46+00:00"
    assert row.fields["notes"] == "signed"
    assert one(conn, "Added", "yesterday").fields["added_at"] is None  # unreadable: ignored
    assert (
        one(conn, "Added", "2024-03-01 08:00:00").fields["added_at"] == "2024-03-01T08:00:00+00:00"
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # as TopDecked writes some rows, with the zone name in the device's language
        (
            "Sat May 03 2025 17:21:51 GMT+0200 (Ora legale dell’Europa centrale)",
            "2025-05-03T15:21:51+00:00",
        ),
        ("Tue Feb 18 2025 23:17:30 GMT-0500 (Eastern Standard Time)", "2025-02-19T04:17:30+00:00"),
        ("Mon Jun 02 2025 22:23:11 GMT+0530", "2025-06-02T16:53:11+00:00"),
        ("May 3 2025 07:05:09 GMT+0000", "2025-05-03T07:05:09+00:00"),
        ("Sat Foo 03 2025 17:21:51 GMT+0200", None),  # no such month
        ("Fri Feb 30 2025 17:21:51 GMT+0200", None),  # no such day
        ("Sat May 03 2025 17:21:51", None),  # no offset: we would have to guess the zone
    ],
)
def test_javascript_dates(conn, value, expected):
    assert one(conn, "Added", f'"{value}"').fields["added_at"] == expected


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
    assert parsed.profile == "moxfield"
    first, second = parsed.rows
    assert first.fields == {
        "quantity": 2,
        "finish": "foil",
        "notes": None,
        "purchase_price": 1.5,
        "added_at": "2024-05-01T10:00:00+00:00",
    }
    assert first.card["set_code"] == "m10"
    assert second.fields["finish"] == "nonfoil"
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
    assert parsed.profile == "manabox"
    first, second = parsed.rows
    assert (first.fields["quantity"], first.fields["purchase_price"]) == (4, 0.5)
    assert (second.fields["finish"], second.fields["quantity"]) == ("etched", 1)


def test_topdecked_sample_has_the_shape_of_the_real_export():
    lines = TOPDECKED.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith('QUANTITY,"NAME",SETCODE,"SETNAME","COLLECTOR NUMBER",FINISH,PRICE,')
    rows = list(csv.reader(lines))
    assert len(rows[0]) == 18 and len(rows) - 1 == 30
    assert {len(row) for row in rows[1:]} == {11}  # the split price, no trailing columns
    assert all(row[6].startswith("€") and len(row[7]) == 2 for row in rows[1:])


def test_topdecked_import(conn):
    parsed = importers.parse_file(TOPDECKED.read_bytes())
    importers.resolve(conn, parsed)
    assert parsed.profile == "topdecked"
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
        "notes": None,
        "purchase_price": None,
        "added_at": "2025-02-11T15:31:46+00:00",
    }
    assert parsed.rows[6].card["name"] == "Jace, the Mind Sculptor"  # a quoted comma

    result = service.import_entries(conn, importers.to_items(parsed), source="topdecked: sample")
    assert (result["entries"], result["copies"]) == (30, 30)
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
        "notes": "signed, front",
        "purchase_price": 0.8,
        "added_at": "2025-01-05T09:00:00+00:00",
    }
    assert whole_price.status == "matched" and whole_price.fields["added_at"]
    assert no_id.status == "matched" and no_id.card["scryfall_id"] == bolt  # set + number
    assert (short.status, short.problem) == ("ambiguous", "ambiguous")


def test_topdecked_rows_with_a_javascript_date(conn):
    """40 of 493 rows of a real binder export carry the acquired date in this shape."""
    bolt = pid(conn, "Lightning Bolt", "m10")
    header = TOPDECKED.read_text(encoding="utf-8").splitlines()[0]
    row = (
        f'1,"Lightning Bolt",m10,"Magic 2010",146,nonfoil,€0,31,common,{bolt},'
        "Sat May 03 2025 17:21:51 GMT+0200 (Ora legale dell’Europa centrale)"
    )
    parsed = importers.parse_file(f"{header}\n{row}\n".encode())
    importers.resolve(conn, parsed)
    (only,) = parsed.rows
    assert parsed.profile == "topdecked" and only.status == "matched"
    assert only.fields["added_at"] == "2025-05-03T15:21:51+00:00"


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
    }

    broken = [dict(items[0]), {**items[1], "finish": "shiny"}]
    before = conn.execute("SELECT COUNT(*), SUM(quantity) FROM entries").fetchone()[:]
    with pytest.raises(service.CollectionError):
        service.import_entries(conn, broken, replace=True)
    assert conn.execute("SELECT COUNT(*), SUM(quantity) FROM entries").fetchone()[:] == before
    assert conn.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 1


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
    client = TestClient(create_app(catalog), follow_redirects=False)
    client.post("/mode", data={"state": "input"})  # what these tests change
    return client


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
    collection = client.get("/stats").text
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
    # nothing to choose but whether to add or replace
    assert 'name="location"' not in page.text and 'name="condition"' not in page.text
    assert 'name="language"' not in page.text and 'name="mode"' in page.text
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
        data={"filename": "x.csv", "mode": "add"},
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
        assert [r[0] for r in conn.execute("SELECT action FROM history")] == ["add", "import"]
    # the backup holds the collection as it was before the import
    import sqlite3

    with closing(sqlite3.connect(catalog.backup_dir / backup)) as before:
        assert before.execute("SELECT SUM(quantity) FROM entries").fetchone()[0] == 2


def _first_id(settings):
    with closing(db.connect(settings)) as conn:
        return pid(conn, "Sol Ring", "c21")


def test_replace_mode(client, catalog):
    client.post("/add", data={"printing": _first_id(catalog), "quantity": "5"})
    page = upload(client, TOPDECKED.read_bytes())
    assert "Remove them first (1 entries)" in page.text
    client.post(f"/import/{token_of(page)}/confirm", data={"mode": "replace"})
    with closing(db.connect(catalog)) as conn:
        assert conn.execute("SELECT SUM(quantity) FROM entries").fetchone()[0] == 30


def test_problem_rows_are_listed_and_downloadable(client, catalog):
    text = (
        "Name,Set code,Collector number,Quantity,Finish\n"
        "Lightning Bolt,m10,146,2,nonfoil\n"
        "Lightning Bolt,,,1,nonfoil\n"
        "Not A Card,,,1,nonfoil\n"
        "Sol Ring,c21,263,three,nonfoil\n"
        "Sol Ring,c21,263,1,shiny\n"
    )
    page = upload(client, text)
    assert "Rows that will not be imported (4)" in page.text
    for message in [
        "6 printings have this name and the file does not say which.",
        "No such card in the catalog.",
        "The quantity is not a whole number from 1 to 9999.",
        "Unknown finish: shiny",
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
        "Finish",
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


def test_confirm_with_a_bad_mode_adds(client, catalog):
    client.post("/add", data={"printing": _first_id(catalog), "quantity": "5"})
    page = upload(client, TOPDECKED.read_bytes())
    client.post(f"/import/{token_of(page)}/confirm", data={"mode": "nonsense"})
    with closing(db.connect(catalog)) as conn:  # nothing was replaced
        assert conn.execute("SELECT SUM(quantity) FROM entries").fetchone()[0] == 35


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
    client.post("/add", data={"printing": _first_id(catalog), "quantity": "3", "finish": "foil"})
    exported = client.get("/export/csv").content

    fresh = editing(TestClient(create_app(other), follow_redirects=False), "input")
    page = upload(fresh, exported, "magic-db-collection.csv")
    assert "magic-db" in page.text
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
    client = editing(TestClient(create_app(settings)), "input")
    assert client.get("/import").status_code == 503
    assert upload(client, "Name\nx\n").status_code == 400
    assert client.get("/export").status_code == 200  # exporting works without it
