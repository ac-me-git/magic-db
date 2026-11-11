"""Catalog ingest against the fixture (SPEC §4)."""

import gzip
import json
import sqlite3
from contextlib import closing

import httpx
import pytest

from magicdb import db
from magicdb.catalog import ingest, publish
from magicdb.cli import main
from tests.conftest import CARDS_JSON, SETS_JSON, build_fixture_catalog

W, U, B, R, G = 1, 2, 4, 8, 16


@pytest.fixture
def conn(catalog):
    with closing(db.connect(catalog)) as connection:
        yield connection


def one(conn: sqlite3.Connection, name: str, set_code: str | None = None) -> sqlite3.Row:
    sql = "SELECT * FROM cat.printings WHERE name LIKE ?"
    params: list[str] = [name + "%"]
    if set_code:
        sql += " AND set_code = ?"
        params.append(set_code)
    rows = conn.execute(sql, params).fetchall()
    assert len(rows) == 1, f"{name}: {len(rows)} rows"
    return rows[0]


# --- mapping rules --------------------------------------------------------------------


def test_every_card_becomes_a_row(conn, fixture_cards):
    assert conn.execute("SELECT COUNT(*) FROM cat.printings").fetchone()[0] == len(fixture_cards)
    meta = dict(conn.execute("SELECT key, value FROM cat.meta"))
    assert meta["row_count"] == str(len(fixture_cards))
    assert meta["bulk_updated_at"] == "file:cards.json"


def test_transform_card_takes_colours_and_images_from_faces(conn):
    delver = one(conn, "Delver of Secrets")
    assert delver["layout"] == "transform"
    assert delver["colors"] == U
    assert delver["mana_cost"] == "{U}"
    assert delver["img_normal"] and delver["img_back_normal"]
    assert delver["img_normal"] != delver["img_back_normal"]
    assert "\n//\n" in delver["oracle_text"]
    assert json.loads(delver["faces_json"])[1]["name"] == "Insectile Aberration"


def test_split_card_joins_text_and_has_no_back_image(conn):
    fire_ice = one(conn, "Fire // Ice")
    assert fire_ice["mana_cost"] == "{1}{R} // {1}{U}"
    assert fire_ice["colors"] == U | R
    assert fire_ice["color_count"] == 2
    assert "\n//\n" in fire_ice["oracle_text"]
    assert fire_ice["img_normal"] and fire_ice["img_back_normal"] is None


def test_star_power_is_not_numeric(conn):
    goyf = one(conn, "Tarmogoyf")
    assert goyf["power"] == "*" and goyf["power_num"] is None
    assert goyf["toughness"] == "1+*" and goyf["toughness_num"] is None
    angel = one(conn, "Serra Angel")
    assert (angel["power_num"], angel["toughness_num"]) == (4.0, 4.0)


def test_colour_bitmasks(conn):
    assert one(conn, "Niv-Mizzet Reborn")["colors"] == W | U | B | R | G
    assert one(conn, "Niv-Mizzet Reborn")["color_count"] == 5
    ring = one(conn, "Sol Ring")
    assert (ring["colors"], ring["color_identity"], ring["color_count"]) == (0, 0, 0)
    assert one(conn, "Boros Reckoner")["color_identity"] == W | R


def test_keywords_and_legalities(conn):
    angel = one(conn, "Baneslayer Angel")
    for keyword in ("flying", "first strike", "lifelink"):
        assert f"|{keyword}|" in angel["keywords"]
    assert one(conn, "Sol Ring")["keywords"] == "|"
    lotus = one(conn, "Black Lotus")
    assert "|vintage|" in lotus["legal_in"]  # restricted counts as legal
    assert "|legacy|" not in lotus["legal_in"]  # banned does not


def test_loyalty_and_defense(conn):
    jace = one(conn, "Jace, the Mind Sculptor")
    assert jace["loyalty"] == "3" and jace["loyalty_num"] == 3.0
    assert one(conn, "Invasion of Zendikar")["defense"] == "3"


def test_normalised_columns(conn):
    row = one(conn, "Jace, the Mind Sculptor")
    assert row["name_norm"] == "jace, the mind sculptor"
    assert row["type_norm"] == row["type_line"].lower()
    assert row["oracle_norm"] == row["oracle_text"].lower()
    assert ingest.normalize("Lim-Dûl's Vault") == "lim-dul's vault"


def test_set_columns_and_collector_sort(conn):
    bolt = one(conn, "Lightning Bolt", "lea")
    assert bolt["year"] == 1993 and bolt["released_at"].startswith("1993-")
    assert bolt["rarity"] == "common" and bolt["rarity_ord"] == 0
    assert bolt["is_reprint"] == 0
    assert one(conn, "Lightning Bolt", "m10")["is_reprint"] == 1
    variant = conn.execute(
        "SELECT collector_number, collector_sort FROM cat.printings "
        "WHERE set_code = 'arn' AND collector_number LIKE '%†'"
    ).fetchone()
    assert variant["collector_sort"] == int(variant["collector_number"].rstrip("†"))


def test_extras_finishes_and_digital(conn):
    tokens = conn.execute("SELECT is_extra FROM cat.printings WHERE layout = 'token'").fetchall()
    assert tokens and all(row["is_extra"] == 1 for row in tokens)
    assert one(conn, "Lightning Bolt", "lea")["is_extra"] == 0
    etched = conn.execute(
        "SELECT COUNT(*) FROM cat.printings WHERE finishes LIKE '%|etched|%'"
    ).fetchone()[0]
    assert etched >= 1
    digital = conn.execute("SELECT is_digital FROM cat.printings WHERE set_code = 'j21'")
    assert all(row["is_digital"] == 1 for row in digital)


def test_prices_are_numbers_or_null(conn):
    kinds = {
        row[0]
        for row in conn.execute("SELECT DISTINCT typeof(price_eur) FROM cat.printings").fetchall()
    }
    assert kinds <= {"real", "null"} and "real" in kinds


def test_pref_picks_the_best_printing(conn):
    best = conn.execute(
        "SELECT set_code, MAX(pref) FROM cat.printings WHERE name = 'Lightning Bolt'"
    ).fetchone()
    assert best["set_code"] == "2x2"  # the newest regular paper printing
    paper_floor = 8 * 100_000_000
    digital = conn.execute("SELECT MAX(pref) FROM cat.printings WHERE is_digital = 1").fetchone()[0]
    paper = conn.execute("SELECT MIN(pref) FROM cat.printings WHERE is_digital = 0").fetchone()[0]
    assert digital < paper_floor <= paper


def test_facets(conn):
    facets = {
        (row["kind"], row["value"]): row["n"]
        for row in conn.execute("SELECT kind, value, n FROM cat.facets")
    }
    for key in [
        ("subtype", "Dragon"),
        ("keyword", "Flying"),
        ("type", "Creature"),
        ("type", "Planeswalker"),
        ("supertype", "Legendary"),
        ("format", "modern"),
    ]:
        assert facets.get(key, 0) > 0, key
    assert ("type", "Legendary") not in facets
    assert ("type", "Token") not in facets  # extras are left out
    # counted once per card, not once per printing
    assert (
        facets[("subtype", "Dragon")]
        == conn.execute(
            "SELECT COUNT(DISTINCT oracle_id) FROM cat.printings "
            "WHERE type_line LIKE '%— %Dragon%' AND is_extra = 0"
        ).fetchone()[0]
    )


def test_reversible_card_takes_everything_from_its_faces():
    face = {
        "name": "Dragonlord Atarka",
        "oracle_id": "o-atarka",
        "mana_cost": "{5}{R}{G}",
        "cmc": 7.0,
        "type_line": "Legendary Creature — Elder Dragon",
        "oracle_text": "Flying, trample",
        "colors": ["G", "R"],
        "power": "8",
        "toughness": "8",
        "image_uris": {"small": "s", "normal": "n"},
    }
    card = {
        "id": "p-1",
        "name": "Dragonlord Atarka // Dragonlord Atarka",
        "layout": "reversible_card",
        "card_faces": [face, {**face, "image_uris": {"small": "bs", "normal": "bn"}}],
        "color_identity": ["G", "R"],
        "set": "sld",
        "collector_number": "1",
        "released_at": "2024-01-01",
    }
    row = ingest.map_card(card)
    assert row["oracle_id"] == "o-atarka"
    assert row["cmc"] == 7.0
    assert row["colors"] == R | G and row["color_count"] == 2
    assert (
        row["type_line"] == "Legendary Creature — Elder Dragon // Legendary Creature — Elder Dragon"
    )
    assert (row["power_num"], row["toughness_num"]) == (8.0, 8.0)
    assert (row["img_normal"], row["img_back_normal"]) == ("n", "bn")


def test_type_tokens():
    assert ingest.type_tokens("Legendary Creature — Elder Dragon") == {
        ("supertype", "Legendary"),
        ("type", "Creature"),
        ("subtype", "Elder"),
        ("subtype", "Dragon"),
    }
    assert ("type", "Land") in ingest.type_tokens("Instant // Land")


def test_names_and_sets(conn, fixture_sets):
    assert (
        conn.execute("SELECT COUNT(*) FROM cat.names WHERE name = 'Lightning Bolt'").fetchone()[0]
        == 1
    )
    assert conn.execute("SELECT COUNT(*) FROM cat.sets").fetchone()[0] == len(fixture_sets)
    arn = conn.execute("SELECT * FROM cat.sets WHERE code = 'arn'").fetchone()
    assert arn["name"] == "Arabian Nights"
    assert arn["catalog_count"] == arn["card_count"] == 92


def test_sets_are_derived_when_no_list_is_given(settings):
    result = ingest.run_update(settings, from_file=CARDS_JSON, min_rows=1)
    assert result.status == "ok"
    with closing(db.connect(settings)) as conn:
        arn = conn.execute("SELECT * FROM cat.sets WHERE code = 'arn'").fetchone()
    assert arn["name"] == "Arabian Nights" and arn["catalog_count"] == 92


# --- publishing -----------------------------------------------------------------------


def test_run_is_recorded(settings):
    catalog = settings
    assert build_fixture_catalog(catalog).status == "ok"
    with closing(db.connect(catalog)) as conn:
        run = conn.execute("SELECT * FROM job_runs").fetchone()
    assert (run["job"], run["status"]) == ("catalog", "ok")
    assert run["finished_at"] and "printings" in run["message"]
    assert list(catalog.tmp_dir.iterdir()) == []


def test_rejected_catalog_leaves_the_old_one_live(catalog, tmp_path, fixture_cards):
    live = publish.live_catalog(catalog)
    small = tmp_path / "small.json"
    small.write_text(json.dumps(fixture_cards[:10]), encoding="utf-8")

    result = ingest.run_update(catalog, from_file=small, min_rows=1)

    assert result.status == "error"
    assert "live catalog has" in result.message
    assert publish.live_catalog(catalog) == live
    assert list(catalog.tmp_dir.iterdir()) == []
    with closing(db.connect(catalog)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM cat.printings").fetchone()[0] > 10
        last = conn.execute("SELECT * FROM job_runs ORDER BY id DESC").fetchone()
    assert last["status"] == "error"


def test_too_small_catalog_is_rejected(settings):
    result = ingest.run_update(settings, from_file=CARDS_JSON)  # default minimum: 50,000
    assert result.status == "error"
    assert publish.live_catalog(settings) is None


def test_broken_file_is_an_error_not_an_exception(settings, tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text('[{"id": "x"', encoding="utf-8")
    result = ingest.run_update(settings, from_file=broken, min_rows=1)
    assert result.status == "error"
    assert publish.live_catalog(settings) is None


def test_publish_while_old_catalog_is_in_use(catalog):
    old = publish.live_catalog(catalog)
    reader = db.connect(catalog)
    try:
        result = build_fixture_catalog(catalog)
        assert result.status == "ok"
        new = publish.live_catalog(catalog)
        assert new != old and new.name > old.name
        # the open connection keeps working on the old file
        assert reader.execute("SELECT COUNT(*) FROM cat.printings").fetchone()[0] > 0
    finally:
        reader.close()
    # once nobody uses it, the old file goes away
    publish.cleanup_old(catalog)
    assert sorted(catalog.catalog_dir.glob("cards-*.db")) == [new]
    with closing(db.connect(catalog)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM cat.printings").fetchone()[0] > 0


# --- downloading ----------------------------------------------------------------------


def scryfall(updated_at: str, calls: list[str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.host == "data.scryfall.io":
            return httpx.Response(200, content=CARDS_JSON.read_bytes())
        if request.url.path == "/bulk-data":
            entries = [
                {"type": "oracle_cards", "updated_at": "x", "download_uri": "https://nope"},
                {
                    "type": "default_cards",
                    "updated_at": updated_at,
                    "download_uri": "https://data.scryfall.io/default-cards/default.json",
                },
            ]
            return httpx.Response(200, json={"data": entries})
        if request.url.path == "/sets":
            return httpx.Response(200, json={"data": json.loads(SETS_JSON.read_text("utf-8"))})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_build_and_skip_when_unchanged(settings):
    calls: list[str] = []
    with scryfall("2026-11-10T22:05:00+00:00", calls) as client:
        first = ingest.run_update(settings, client=client, min_rows=1)
        assert first.status == "ok"
        assert calls == ["/bulk-data", "/sets", "/default-cards/default.json"]
        assert publish.read_meta(first.path)["bulk_updated_at"] == "2026-11-10T22:05:00+00:00"

        calls.clear()
        second = ingest.run_update(settings, client=client, min_rows=1)
        assert second.status == "skipped"
        assert calls == ["/bulk-data"]  # nothing downloaded
        assert publish.live_catalog(settings) == first.path

        forced = ingest.run_update(settings, client=client, min_rows=1, force=True)
        assert forced.status == "ok" and forced.path != first.path
    assert list(settings.tmp_dir.iterdir()) == []


def test_newer_bulk_file_replaces_the_catalog(settings):
    with scryfall("2026-11-10T22:05:00+00:00", []) as client:
        first = ingest.run_update(settings, client=client, min_rows=1)
    with scryfall("2026-11-11T22:05:00+00:00", []) as client:
        second = ingest.run_update(settings, client=client, min_rows=1)
    assert second.status == "ok"
    assert publish.live_catalog(settings) == second.path != first.path


def test_network_failure_is_an_error_not_an_exception(catalog):
    live = publish.live_catalog(catalog)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    with client:
        result = ingest.run_update(catalog, client=client, min_rows=1)
    assert result.status == "error" and "503" in result.message
    assert publish.live_catalog(catalog) == live


def as_json_lines(cards: list[dict]) -> bytes:
    return "\n".join(json.dumps(card) for card in cards).encode() + b"\n"


@pytest.mark.parametrize("compress", [False, True])
def test_json_lines_file_is_read_like_the_array(settings, tmp_path, fixture_cards, compress):
    data = as_json_lines(fixture_cards)
    source = tmp_path / "cards.download"  # the name says nothing about the format
    source.write_bytes(gzip.compress(data) if compress else data)
    assert list(ingest.iter_cards(source)) == list(ingest.iter_cards(CARDS_JSON))
    result = ingest.run_update(settings, from_file=source, sets_file=SETS_JSON, min_rows=1)
    assert result.status == "ok" and result.message == f"{len(fixture_cards)} printings"


def test_download_of_gzipped_json_lines(settings, fixture_cards):
    """The shape Scryfall serves since late 2026: jsonl_download_uri, no download_uri."""
    payload = gzip.compress(as_json_lines(fixture_cards))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "data.scryfall.io":
            return httpx.Response(200, content=payload)
        if request.url.path == "/bulk-data":
            entry = {
                "type": "default_cards",
                "updated_at": "2026-11-11T10:06:45.392+00:00",
                "jsonl_download_uri": "https://data.scryfall.io/default-cards/x.jsonl.gz",
                "compressed_size": len(payload),
            }
            return httpx.Response(200, json={"data": [entry]})
        return httpx.Response(200, json={"data": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = ingest.run_update(settings, client=client, min_rows=1)
    assert result.status == "ok" and result.message == f"{len(fixture_cards)} printings"
    assert list(settings.tmp_dir.iterdir()) == []


def test_bulk_entry_without_a_link_is_an_error(settings):
    entry = {"type": "default_cards", "updated_at": "x"}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"data": [entry]}))
    with httpx.Client(transport=transport) as client:
        result = ingest.run_update(settings, client=client, min_rows=1)
    assert result.status == "error" and "no download link" in result.message


# --- command line ---------------------------------------------------------------------


def test_cli_update_from_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MAGICDB_DATA_DIR", str(tmp_path / "data"))
    args = ["update", "--from-file", str(CARDS_JSON), "--sets-file", str(SETS_JSON)]
    assert main([*args, "--min-rows", "1"]) == 0
    assert "catalog update: ok" in capsys.readouterr().out
    assert len(list((tmp_path / "data" / "catalog").glob("cards-*.db"))) == 1
    assert main(args) == 1  # the default minimum rejects a 263-card catalog
    assert "catalog update: error" in capsys.readouterr().out
