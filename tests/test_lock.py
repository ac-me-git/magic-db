"""The safety lock: a locked collection can be read but not changed (SPEC §7)."""

import sqlite3
import time
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from magicdb import db, jobs, lock
from magicdb.app import create_app
from tests.conftest import FIXTURES

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
    """An unlocked collection with one location and three Lightning Bolts in it."""
    client.post("/locations", data={"name": "Binder", "kind": "binder"})
    client.post("/add", data={"printing": bolt, "quantity": "3", "location": "1"})
    return client


def set_lock(client, state, next_page=""):
    return client.post("/lock", data={"state": state, "next": next_page})


def snapshot(settings):
    """The whole collection, to prove that nothing changed."""
    with closing(db.connect(settings)) as conn:
        return {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]
            for table in ("entries", "locations", "history")
        }


# --- the switch -----------------------------------------------------------------------


def test_a_new_collection_is_unlocked(client, catalog):
    assert lock.load(catalog) is False
    page = client.get("/").text
    assert 'class="lock-switch is-unlocked"' in page
    assert "Collection unlocked: changes allowed" in page
    assert 'name="state" value="on"' in page and 'title="Lock"' in page


def test_locking_and_unlocking(client, catalog):
    response = set_lock(client, "on", "/collection?sort=name")
    assert (response.status_code, response.headers["location"]) == (303, "/collection?sort=name")
    assert lock.load(catalog) is True
    page = client.get("/collection?sort=name").text
    assert 'class="lock-switch is-locked"' in page and "Collection locked: read only" in page
    assert 'name="state" value="off"' in page and 'title="Unlock"' in page
    assert 'name="next" value="/collection?sort=name"' in page  # comes back to this page

    assert set_lock(client, "off").headers["location"] == "/collection"
    assert lock.load(catalog) is False
    assert "is-unlocked" in client.get("/").text


def test_the_lock_survives_a_restart(client, catalog):
    set_lock(client, "on")
    restarted = TestClient(create_app(catalog))  # a new process reads the saved state
    assert restarted.app.state.locked is True
    assert "is-locked" in restarted.get("/").text
    set_lock(restarted, "off")
    assert TestClient(create_app(catalog)).app.state.locked is False


@pytest.mark.parametrize("state", ["", "maybe", "ON", "1"])
def test_a_meaningless_state_changes_nothing(client, catalog, state):
    set_lock(client, "on")
    assert set_lock(client, state).status_code == 303
    assert lock.load(catalog) is True


def test_switch_redirect_stays_on_this_site(client):
    assert set_lock(client, "on", "https://evil.example/").headers["location"] == "/collection"


def test_the_bar_is_on_every_page(stocked, bolt):
    for path in (
        "/",
        "/search?q=bolt",
        f"/card/{bolt}",
        "/collection",
        "/locations",
        "/add",
        "/import",
        "/export",
        "/stats",
        "/admin",
        "/syntax",
        "/nothing-here",
    ):
        assert 'action="/lock"' in stocked.get(path).text, path
    partial = stocked.get("/search?q=bolt", headers={"HX-Request": "true"}).text
    assert 'action="/lock"' not in partial  # a fragment has no page chrome


# --- a locked collection cannot be changed --------------------------------------------

CHANGES = [
    ("/add", {"printing": "BOLT", "quantity": "1"}),
    ("/collection/1", {"quantity": "9", "finish": "foil", "condition": "NM", "language": "en"}),
    ("/collection/1/move", {"quantity": "1", "location": ""}),
    ("/collection/1/remove", {"quantity": "1"}),
    ("/collection/1/remove", {}),
    ("/locations", {"name": "Box", "kind": "box"}),
    ("/locations/1", {"name": "Renamed", "kind": "box"}),
    ("/locations/1/shift", {"offset": "1"}),
    ("/locations/1/empty", {"destination": ""}),
    ("/locations/1/delete", {}),
    ("/import/0123456789abcdef0123456789abcdef/confirm", {"mode": "replace"}),
    ("/some/page/that/does/not/exist/yet", {}),
]


@pytest.mark.parametrize(("path", "data"), CHANGES, ids=[path for path, _ in CHANGES])
def test_every_change_is_refused_while_locked(stocked, catalog, bolt, path, data):
    set_lock(stocked, "on")
    before = snapshot(catalog)
    data = {key: bolt if value == "BOLT" else value for key, value in data.items()}

    refused = stocked.post(path, data=data)
    assert refused.status_code == 423
    assert "The collection is locked" in refused.text and "Nothing was changed." in refused.text
    assert 'name="state" value="off"' in refused.text  # the way out is on the page
    assert snapshot(catalog) == before


def test_importing_is_refused_while_locked(stocked, catalog):
    set_lock(stocked, "on")
    before = snapshot(catalog)
    upload = stocked.post("/import", files={"file": ("x.csv", TOPDECKED.read_bytes(), "text/csv")})
    assert upload.status_code == 423
    assert not list(catalog.tmp_dir.glob("import-*")) if catalog.tmp_dir.exists() else True
    assert snapshot(catalog) == before


def test_other_methods_are_refused_too(stocked, catalog):
    set_lock(stocked, "on")
    for method in ("put", "patch", "delete"):
        assert getattr(stocked, method)("/collection/1").status_code == 423


def test_the_same_changes_work_again_once_unlocked(stocked, catalog, bolt):
    set_lock(stocked, "on")
    assert stocked.post("/add", data={"printing": bolt, "quantity": "2"}).status_code == 423
    set_lock(stocked, "off")
    assert (
        stocked.post("/add", data={"printing": bolt, "quantity": "2", "location": "1"}).status_code
        == 303
    )
    assert snapshot(catalog)["entries"][0][6] == 5  # quantity: 3 + 2


# --- but it can still be read, exported and backed up ---------------------------------


def test_reading_works_while_locked(stocked, bolt):
    set_lock(stocked, "on")
    for path in (
        "/",
        "/search?q=bolt",
        "/search?q=have:yes",
        f"/card/{bolt}",
        "/collection",
        "/collection?q=bolt&sort=value",
        "/locations",
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
    set_lock(stocked, "on")
    assert stocked.get("/lang/it").status_code == 303
    assert "Collezione bloccata: sola lettura" in stocked.get("/collection").text


def test_backups_and_catalog_updates_work_while_locked(stocked, catalog, monkeypatch):
    monkeypatch.setitem(jobs.RUNNERS, "catalog", lambda settings: jobs.JobResult("ok", "fake"))
    set_lock(stocked, "on")
    assert stocked.post("/admin/backup/run").headers["location"] == "/admin?started=yes"
    assert stocked.post("/admin/catalog/run").headers["location"] == "/admin?started=yes"
    for _ in range(100):  # the backup runs in the background: give it a moment
        if not jobs.is_running("backup") and jobs.list_backups(catalog):
            break
        time.sleep(0.05)
    assert [p for p in jobs.list_backups(catalog) if p.suffix == ".db"]
    assert jobs.run_backup(catalog).status == "ok"  # and the nightly one would work too


# --- the pages do not offer what would be refused -------------------------------------


def test_pages_hide_their_forms_while_locked(stocked, bolt):
    open_pages = {
        "/add": 'action="/add"',
        "/collection": "/collection/1/edit",
        f"/card/{bolt}": 'name="printing"',
        "/locations": 'action="/locations"',
        "/import": 'enctype="multipart/form-data"',
        "/collection/1/edit": 'action="/collection/1"',
    }
    for path, control in open_pages.items():
        assert control in stocked.get(path).text, path

    set_lock(stocked, "on")
    for path, control in open_pages.items():
        page = stocked.get(path).text
        assert control not in page, path
        assert "Lightning Bolt" in page or path in ("/add", "/locations", "/import"), path
    assert 'action="/locations/1' not in stocked.get("/locations").text  # no rename, no delete
    assert "Binder" in stocked.get("/locations").text  # still listed
    for path in ("/add", "/locations", "/import", "/collection/1/edit"):
        page = stocked.get(path).text
        assert "so nothing can change them by accident" in page, path
    assert "Edit</a>" not in stocked.get(f"/card/{bolt}").text
    assert ">3</td>" in stocked.get(f"/card/{bolt}").text  # my copies are still shown


def test_unlocking_from_a_notice_comes_back_to_that_page(stocked):
    set_lock(stocked, "on")
    page = stocked.get("/add?name=Lightning Bolt").text
    notice = page.split('class="notice locked-notice"')[1].split("</form>")[0]
    assert 'name="next" value="/add?name=Lightning%20Bolt"' in notice
    back = set_lock(stocked, "off", "/add?name=Lightning%20Bolt").headers["location"]
    assert back == "/add?name=Lightning%20Bolt"
    assert "Printing (6)" in stocked.get(back).text  # the form is there again, name kept


# --- the lock itself is not open to other sites, and respects the password ------------


def test_another_site_cannot_unlock(stocked, catalog):
    set_lock(stocked, "on")
    refused = stocked.post(
        "/lock", data={"state": "off"}, headers={"Origin": "https://evil.example"}
    )
    assert refused.status_code == 403 and lock.load(catalog) is True


def test_unlocking_is_a_post(client):
    assert client.get("/lock").status_code in (404, 405)


# --- upgrading a database made before the lock existed --------------------------------


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
        assert conn.execute("SELECT name FROM locations").fetchone()[0] == "Old binder"
        assert conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0
    assert client.app.state.locked is False  # an upgraded collection starts unlocked

    (backup,) = settings.backup_dir.glob("pre-migration-*.db")
    with closing(sqlite3.connect(backup)) as copy:
        assert copy.execute("PRAGMA user_version").fetchone()[0] == 1
        assert copy.execute("SELECT name FROM locations").fetchone()[0] == "Old binder"
        tables = {row[0] for row in copy.execute("SELECT name FROM sqlite_master")}
        assert "settings" not in tables
