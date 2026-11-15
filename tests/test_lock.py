"""The switch of what may change: locked, collection input, deck building (SPEC §7)."""

import re
import sqlite3
import time
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from magicdb import db, jobs, lock
from magicdb.app import create_app
from tests.conftest import FIXTURES, editing

TOPDECKED = FIXTURES / "topdecked-sample.csv"


@pytest.fixture
def client(catalog):
    return TestClient(create_app(catalog), follow_redirects=False)


@pytest.fixture
def bolt(fixture_cards):
    return next(
        c["id"] for c in fixture_cards if c["name"] == "Lightning Bolt" and c["set"] == "m10"
    )


@pytest.fixture
def stocked(client, bolt):
    """Three Lightning Bolts and a deck, and the switch back on locked."""
    editing(client, "input").post("/add", data={"printing": bolt, "quantity": "3"})
    editing(client, "deck").post("/decks", data={"name": "Burn"})
    return editing(client, "locked")


def set_mode(client, state, next_page=""):
    return client.post("/mode", data={"state": state, "next": next_page})


def pressed(page):
    """The position of the switch above the search results."""
    bar = page.split('<div class="views">')[1].split("</form>")[0]
    return re.findall(r'value="(\w+)" aria-pressed="true"', bar)


def snapshot(settings):
    """The whole collection, to prove that nothing changed."""
    with closing(db.connect(settings)) as conn:
        return {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]
            for table in ("entries", "decks", "deck_cards", "history")
        }


# --- the switch -----------------------------------------------------------------------


def test_a_new_database_starts_locked(client, catalog):
    assert lock.load(catalog) == "locked"
    page = client.get("/search?q=bolt").text
    assert pressed(page) == ["locked"]
    assert 'aria-label="What may change"' in page
    for label in ('aria-label="Locked"', ">Collection input</span>", ">Deck building</span>"):
        assert label in page, label
    # the button that opens the menu says it is locked, on every page
    name = "Menu · Locked"
    assert f'<summary title="{name}" aria-label="{name}">' in page
    button = page.split('<details class="menu">')[1].split("</summary>")[0]
    assert '<span class="menu-lock"><svg' in button


@pytest.mark.parametrize("state", ["input", "deck", "locked"])
def test_moving_the_switch(client, catalog, state):
    response = set_mode(client, state, "/search?q=bolt&view=list")
    assert response.status_code == 303
    assert response.headers["location"] == "/search?q=bolt&view=list"
    assert lock.load(catalog) == state
    page = client.get("/search?q=bolt&view=list").text
    assert pressed(page) == [state]
    assert 'name="next" value="/search?q=bolt&amp;view=list"' in page  # comes back here
    assert ("menu-lock" in client.get("/stats").text) == (state == "locked")


def test_the_switch_survives_a_restart(client, catalog):
    set_mode(client, "deck")
    restarted = TestClient(create_app(catalog))  # a new process reads the saved state
    assert restarted.app.state.edit_mode == "deck"
    assert pressed(restarted.get("/search?q=bolt").text) == ["deck"]


@pytest.mark.parametrize("state", ["", "maybe", "INPUT", "1", "on", "off"])
def test_a_meaningless_state_changes_nothing(client, catalog, state):
    set_mode(client, "input")
    assert set_mode(client, state).status_code == 303
    assert lock.load(catalog) == "input"


def test_switch_redirect_stays_on_this_site(client):
    assert set_mode(client, "input", "https://evil.example/").headers["location"] == "/"


def test_the_switch_is_above_the_search_results_and_on_the_card_page(stocked, bolt):
    assert stocked.get("/search?q=bolt").text.count('class="slider edit-switch"') == 1
    partial = stocked.get("/search?q=bolt", headers={"HX-Request": "true"}).text
    assert 'class="menu' not in partial  # a fragment has no page chrome
    assert partial.count('class="slider edit-switch"') == 1  # but the results bar has it
    for path in ("/", "/stats", "/admin", "/syntax"):
        assert "edit-switch" not in stocked.get(path).text, path
    assert stocked.get(f"/card/{bolt}").text.count('class="slider edit-switch"') == 1


def test_the_old_lock_is_gone(client):
    assert client.post("/lock", data={"state": "off"}).status_code == 423
    assert client.get("/lock").status_code in (404, 405)


# --- what each position refuses -------------------------------------------------------

COLLECTION_CHANGES = [
    ("/add", {"printing": "BOLT", "quantity": "1"}),
    ("/collection/1", {"quantity": "9", "finish": "foil"}),
    ("/collection/1/remove", {"quantity": "1"}),
    ("/collection/1/remove", {}),
    ("/fast/toggle", {"printing": "BOLT", "finish": "foil", "action": "add"}),
    ("/import/0123456789abcdef0123456789abcdef/confirm", {"mode": "replace"}),
    ("/some/page/that/does/not/exist/yet", {}),
]
DECK_CHANGES = [
    ("/deckmode/card", {"deck": "1", "printing": "BOLT", "action": "add"}),
    ("/decks", {"name": "Control"}),
    ("/decks/1", {"name": "Renamed"}),
    ("/deck-cards", {"deck": "1", "printing": "BOLT", "quantity": "1"}),
]
CASES = [(state, "input", *change) for state in ("locked", "deck") for change in COLLECTION_CHANGES]
CASES += [(state, "deck", *change) for state in ("locked", "input") for change in DECK_CHANGES]


@pytest.mark.parametrize(
    ("state", "needed", "path", "data"),
    CASES,
    ids=[f"{state}:{path}" for state, _, path, _ in CASES],
)
def test_a_change_is_refused_unless_the_switch_allows_it(
    stocked, catalog, bolt, state, needed, path, data
):
    editing(stocked, state)
    before = snapshot(catalog)
    data = {key: bolt if value == "BOLT" else value for key, value in data.items()}

    refused = stocked.post(path, data=data)
    assert refused.status_code == 423
    assert "Protected from changes" in refused.text and "Nothing was changed." in refused.text
    # the way out is on the page: the position that would allow it
    notice = refused.text.split('class="notice locked-notice"')[1].split("</form>")[0]
    assert f'name="state" value="{needed}"' in notice
    assert snapshot(catalog) == before


def test_importing_is_refused_out_of_collection_input(stocked, catalog):
    before = snapshot(catalog)
    upload = stocked.post("/import", files={"file": ("x.csv", TOPDECKED.read_bytes(), "text/csv")})
    assert upload.status_code == 423
    assert not list(catalog.tmp_dir.glob("import-*")) if catalog.tmp_dir.exists() else True
    assert snapshot(catalog) == before


def test_other_methods_are_refused_too(stocked):
    for method in ("put", "patch", "delete"):
        assert getattr(stocked, method)("/collection/1").status_code == 423
        assert getattr(stocked, method)("/decks/1").status_code == 423


def test_each_position_allows_its_own_changes(stocked, catalog, bolt):
    assert stocked.post("/add", data={"printing": bolt, "quantity": "2"}).status_code == 423
    editing(stocked, "input")
    assert stocked.post("/add", data={"printing": bolt, "quantity": "2"}).status_code == 303
    assert snapshot(catalog)["entries"][0][6] == 5  # quantity: 3 + 2
    editing(stocked, "deck")
    assert stocked.post("/decks/1", data={"name": "Burn 2"}).status_code == 303
    assert snapshot(catalog)["decks"][0][1] == "Burn 2"


# --- whatever the position, everything can be read, exported and backed up ------------


def test_reading_works_while_locked(stocked, bolt):
    for path in (
        "/",
        "/search?q=bolt",
        "/search?q=have:yes",
        f"/card/{bolt}",
        "/decks",
        "/stats",
        "/export",
        "/admin",
        "/syntax",
        "/api/autocomplete?q=bolt",
        "/filter?name=bolt",
        f"/img/small/{bolt}/back",
    ):
        assert stocked.get(path).status_code in (200, 303), path
    assert "×3</span>" in stocked.get('/search?q=!"Lightning Bolt"').text  # badges still show
    assert stocked.get("/export/csv").content.count(b"Lightning Bolt") == 1
    assert len(stocked.get("/export/json").json()["entries"]) == 1


def test_changing_the_language_works_while_locked(stocked):
    assert stocked.get("/lang/it").status_code == 303
    page = stocked.get("/search?q=bolt").text
    assert 'aria-label="Cosa si può modificare"' in page and 'aria-label="Bloccato"' in page
    assert ">Inserimento collezione</span>" in page and ">Costruzione mazzi</span>" in page


def test_backups_and_catalog_updates_work_while_locked(stocked, catalog, monkeypatch):
    monkeypatch.setitem(jobs.RUNNERS, "catalog", lambda settings: jobs.JobResult("ok", "fake"))
    assert stocked.post("/admin/backup/run").headers["location"] == "/admin?started=yes"
    assert stocked.post("/admin/catalog/run").headers["location"] == "/admin?started=yes"
    for _ in range(100):  # the backup runs in the background: give it a moment
        if not jobs.is_running("backup") and jobs.list_backups(catalog):
            break
        time.sleep(0.05)
    assert [p for p in jobs.list_backups(catalog) if p.suffix == ".db"]
    assert jobs.run_backup(catalog).status == "ok"  # and the nightly one would work too


# --- the pages do not offer what would be refused -------------------------------------

COLLECTION_FORMS = {
    "/card/BOLT": 'action="/add"',
    "/import": 'enctype="multipart/form-data"',
    "/collection/1/edit": 'action="/collection/1"',
}
DECK_FORMS = {
    "/decks/1": 'action="/decks/1/cards"',
    "/decks": 'action="/decks"',
    "/card/BOLT": 'action="/deck-cards"',
}


def page_of(client, path, bolt):
    return client.get(path.replace("BOLT", bolt)).text


@pytest.mark.parametrize("state", ["locked", "input", "deck"])
def test_pages_offer_only_what_the_switch_allows(stocked, bolt, state):
    editing(stocked, state)
    for forms, open_in in ((COLLECTION_FORMS, "input"), (DECK_FORMS, "deck")):
        for path, control in forms.items():
            assert (control in page_of(stocked, path, bolt)) == (state == open_in), (path, state)
    card = page_of(stocked, "/card/BOLT", bolt)
    assert ">3</td>" in card  # my copies are always shown
    assert ("Edit</a>" in card) == (state == "input")
    assert "Burn" in stocked.get("/decks/1").text


def test_protected_pages_say_how_to_change_them(stocked):
    for path, needed, text in (
        ("/import", "input", "The collection is protected"),
        ("/collection/1/edit", "input", "The collection is protected"),
        ("/decks/1", "deck", "The decks are protected"),
        ("/decks", "deck", "The decks are protected"),
    ):
        page = stocked.get(path).text
        notice = page.split('class="notice locked-notice"')[1].split("</form>")[0]
        assert text in notice and f'name="state" value="{needed}"' in notice, path


def test_switching_from_a_notice_comes_back_to_that_page(stocked):
    page = stocked.get("/collection/1/edit?next=%2Fstats").text
    notice = page.split('class="notice locked-notice"')[1].split("</form>")[0]
    assert 'name="next" value="/collection/1/edit?next=%2Fstats"' in notice
    back = set_mode(stocked, "input", "/collection/1/edit?next=%2Fstats").headers["location"]
    assert back == "/collection/1/edit?next=%2Fstats"
    assert 'action="/collection/1"' in stocked.get(back).text  # the form is there again


# --- the switch itself is not open to other sites -------------------------------------


def test_another_site_cannot_move_the_switch(stocked, catalog):
    refused = stocked.post(
        "/mode", data={"state": "input"}, headers={"Origin": "https://evil.example"}
    )
    assert refused.status_code == 403 and lock.load(catalog) == "locked"


def test_moving_the_switch_is_a_post(client, catalog):
    assert client.get("/mode?state=input").status_code in (404, 405)
    assert lock.load(catalog) == "locked"


# --- upgrading a database made before the switch existed ------------------------------


def test_upgrade_adds_the_settings_table_and_backs_up_first(settings):
    settings.data_dir.mkdir(parents=True)
    first = (db.MIGRATIONS_DIR / "0001_init.sql").read_text(encoding="utf-8")
    with closing(sqlite3.connect(settings.collection_path)) as old:
        old.executescript(f"BEGIN;\n{first}\nPRAGMA user_version = 1;\nCOMMIT;")
        old.execute("INSERT INTO locations (name, created_at) VALUES ('Old binder', 'x')")
        old.commit()

    client = TestClient(create_app(settings))  # starting the app upgrades the database
    with closing(db.connect(settings)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 2
        assert conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0
    assert client.app.state.edit_mode == "locked"  # an upgraded collection starts locked

    (backup,) = settings.backup_dir.glob("pre-migration-*.db")
    with closing(sqlite3.connect(backup)) as copy:
        assert copy.execute("PRAGMA user_version").fetchone()[0] == 1
        assert copy.execute("SELECT name FROM locations").fetchone()[0] == "Old binder"
        tables = {row[0] for row in copy.execute("SELECT name FROM sqlite_master")}
        assert "settings" not in tables


def test_a_database_saved_with_the_old_lock_starts_locked(catalog):
    with closing(db.connect(catalog)) as conn, conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('locked', '0')")
    assert TestClient(create_app(catalog)).app.state.edit_mode == "locked"
