"""Automatic jobs: due logic, backup, rotation, scheduler, admin page (SPEC §8)."""

import base64
import calendar
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from magicdb import db, jobs
from magicdb.app import create_app
from magicdb.catalog import ingest
from magicdb.collection import exporters, service
from magicdb.config import ConfigError, load_settings
from tests.conftest import CARDS_JSON, SETS_JSON


def at(text: str) -> datetime:
    return datetime.fromisoformat(text)


def nightly(moment: datetime) -> list[str]:
    stamp = moment.strftime("%Y%m%d-%H%M%S")
    return [f"collection-{stamp}.db", f"collection-{stamp}.csv"]


def stamp_of(name: str) -> datetime:
    return datetime.strptime(name[len("collection-") : -len(".db")], "%Y%m%d-%H%M%S")


# --- rotation: a pure function --------------------------------------------------------


def test_rotation_over_a_simulated_year():
    start = at("2025-01-01T04:00:00")
    names: set[str] = set()
    for day in range(400):
        now = start + timedelta(days=day)
        names.update(nightly(now))
        doomed = jobs.plan_rotation(sorted(names), now)
        assert not set(doomed) & set(nightly(now))  # never the one just made
        for name in doomed:  # never anything from the last 7 days
            assert now - stamp_of(name.replace(".csv", ".db")) >= jobs.KEEP_DAILY
        names -= set(doomed)

    now = start + timedelta(days=399)  # 2026-02-04
    kept = sorted(stamp_of(n) for n in names if n.endswith(".db"))
    assert {n[:-3] for n in names if n.endswith(".db")} == {
        n[:-4] for n in names if n.endswith(".csv")
    }

    last_week = [now - timedelta(days=d) for d in range(7)]
    assert set(last_week) <= set(kept)  # every one of the last 7 days
    weeks = {(now - timedelta(days=d)).isocalendar()[:2] for d in range(28)}
    assert weeks <= {s.isocalendar()[:2] for s in kept}  # one for each of the last 4 weeks

    months = {(s.year, s.month) for s in kept}
    expected_months = {
        ((now.year * 12 + now.month - 1 - back) // 12, (now.month - 1 - back) % 12 + 1)
        for back in range(12)
    }
    assert months == expected_months  # the last 12 months, and nothing older
    for year, month in expected_months - {(now.year, now.month)}:
        last_day = calendar.monthrange(year, month)[1]
        assert datetime(year, month, last_day, 4) in kept  # the newest of each month

    older = [s for s in kept if now - s >= jobs.KEEP_WEEKLY]
    assert all(s.day == calendar.monthrange(s.year, s.month)[1] for s in older)
    assert 7 <= len(kept) <= 7 + 4 + 12


def test_rotation_of_safety_copies_and_foreign_files():
    now = at("2026-06-15T04:00:00")
    names = [
        "pre-import-20260601-120000.db",  # 14 days: kept
        "pre-import-20260516-040001.db",  # just under 30 days: kept
        "pre-import-20260515-120000.db",  # over 30 days: deleted
        "pre-migration-20260101-000000.db",
        "pre-migration-20260610-000000.db",
        "collection.db",  # not ours to touch
        "collection-bad-name.db",
        "notes.txt",
        "collection-20260614-040000.db",
        "collection-20260614-040000.csv",
    ]
    before = list(names)
    assert jobs.plan_rotation(names, now) == [
        "pre-import-20260515-120000.db",
        "pre-migration-20260101-000000.db",
    ]
    assert names == before  # the input is not changed
    assert jobs.plan_rotation(names, now) == jobs.plan_rotation(list(reversed(names)), now)
    assert jobs.plan_rotation([], now) == []


def test_rotation_keeps_the_newest_of_several_on_one_day():
    now = at("2026-06-15T04:00:00")
    old_day = [*nightly(at("2026-03-31T04:00:00")), *nightly(at("2026-03-31T18:30:00"))]
    assert jobs.plan_rotation(old_day, now) == sorted(nightly(at("2026-03-31T04:00:00")))
    recent = [*nightly(at("2026-06-14T04:00:00")), *nightly(at("2026-06-14T18:30:00"))]
    assert jobs.plan_rotation(recent, now) == []  # within 7 days nothing goes


# --- when a job is due ----------------------------------------------------------------


def test_due_on_a_normal_night():
    yesterday = at("2026-06-14T03:00:20")
    assert not jobs.is_due(at("2026-06-15T02:59:00"), 3, yesterday, yesterday)
    assert jobs.is_due(at("2026-06-15T03:00:00"), 3, yesterday, yesterday)
    assert jobs.is_due(at("2026-06-15T09:30:00"), 3, yesterday, yesterday)


def test_not_due_again_once_done_today():
    done = at("2026-06-15T03:00:40")
    for later in ("2026-06-15T03:01:00", "2026-06-15T12:00:00", "2026-06-15T23:59:00"):
        assert not jobs.is_due(at(later), 3, done, done)
    assert not jobs.is_due(at("2026-06-16T02:00:00"), 3, done, done)  # tomorrow, too early
    assert jobs.is_due(at("2026-06-16T03:00:00"), 3, done, done)


def test_due_after_the_pc_was_off_for_three_days():
    last = at("2026-06-12T03:00:20")
    assert jobs.is_due(at("2026-06-15T14:00:00"), 3, last, last)
    assert jobs.is_due(at("2026-06-15T01:00:00"), 3, last, last)  # even before its hour
    # 36 hours is the limit: a success yesterday afternoon is fine until tonight's hour
    afternoon = at("2026-06-14T15:00:00")
    assert not jobs.is_due(at("2026-06-15T01:00:00"), 3, afternoon, afternoon)
    assert jobs.is_due(at("2026-06-15T03:00:00"), 3, afternoon, afternoon)
    assert jobs.is_due(at("2026-06-16T03:00:01"), 23, afternoon, afternoon)  # 36 h passed


def test_not_due_while_running():
    long_ago = at("2026-01-01T03:00:00")
    assert jobs.is_due(at("2026-06-15T03:00:00"), 3, long_ago, long_ago)
    assert not jobs.is_due(at("2026-06-15T03:00:00"), 3, long_ago, long_ago, running=True)
    assert not jobs.is_due(at("2026-06-15T03:00:00"), 3, None, None, running=True)


def test_first_run_and_retries():
    now = at("2026-06-15T10:00:00")
    assert jobs.is_due(now, 3, None, None)  # never ran: now
    assert not jobs.is_due(now, 3, None, now - timedelta(minutes=10))  # just failed: wait
    assert not jobs.is_due(now, 3, None, now - timedelta(minutes=59))
    assert jobs.is_due(now, 3, None, now - timedelta(minutes=60))
    old = at("2026-06-13T03:00:00")
    assert not jobs.is_due(now, 3, old, now - timedelta(minutes=5))
    assert jobs.is_due(now, 3, old, now + timedelta(hours=5))  # a clock set back does not block


# --- the scheduler, with a clock we control -------------------------------------------


class Clock:
    def __init__(self, start: str) -> None:
        self.now = at(start)

    def __call__(self) -> datetime:
        return self.now

    def utc(self) -> str:
        return self.now.astimezone(UTC).isoformat(timespec="seconds")


@pytest.fixture
def clock():
    return Clock("2026-06-15T00:00:00")


class Calls(list):
    """The jobs that ran, as (name, time); `outcome` sets what they report."""

    outcome: dict[str, str]


@pytest.fixture
def ran(monkeypatch, clock):
    """Replace the real jobs with ones that only record that they ran, at clock time."""
    calls = Calls()
    outcome = {"catalog": "ok", "backup": "ok"}

    def fake(job):
        def run(settings):
            calls.append((job, clock.now))
            with closing(db.connect(settings)) as conn, conn:
                conn.execute(
                    "INSERT INTO job_runs (job, started_at, finished_at, status) VALUES (?, ?, ?, ?)",
                    (job, clock.utc(), clock.utc(), outcome[job]),
                )
            return jobs.JobResult(outcome[job], "")

        return run

    monkeypatch.setitem(jobs.RUNNERS, "catalog", fake("catalog"))
    monkeypatch.setitem(jobs.RUNNERS, "backup", fake("backup"))
    calls.outcome = outcome
    return calls


def advance(scheduler, clock, minutes):
    for _ in range(minutes):
        clock.now += timedelta(minutes=1)
        scheduler.tick()


def test_scheduler_runs_each_job_once_a_night(catalog, clock, ran):
    scheduler = jobs.Scheduler(catalog, clock=clock)
    assert scheduler.tick() == ["catalog", "backup"]  # nothing has ever run: both now
    ran.clear()
    advance(scheduler, clock, 3 * 24 * 60)  # three days, minute by minute
    assert ran == [
        ("catalog", at("2026-06-16T03:00:00")),
        ("backup", at("2026-06-16T04:00:00")),
        ("catalog", at("2026-06-17T03:00:00")),
        ("backup", at("2026-06-17T04:00:00")),
    ]  # the 15th was done by the first tick; the clock stops at midnight on the 18th


def test_scheduler_catches_up_after_days_off(catalog, clock, ran):
    scheduler = jobs.Scheduler(catalog, clock=clock)
    scheduler.tick()
    ran.clear()
    clock.now = at("2026-06-18T17:25:00")  # switched back on three days later
    assert scheduler.tick() == ["catalog", "backup"]
    advance(scheduler, clock, 6 * 60)  # the rest of the day: nothing more
    assert len(ran) == 2


def test_scheduler_waits_an_hour_after_a_failure(catalog, clock, ran):
    ran.outcome["catalog"] = "error"
    scheduler = jobs.Scheduler(catalog, clock=clock)
    scheduler.tick()
    advance(scheduler, clock, 150)
    catalog_runs = [moment for job, moment in ran if job == "catalog"]
    assert catalog_runs == [
        at("2026-06-15T00:00:00"),
        at("2026-06-15T01:00:00"),
        at("2026-06-15T02:00:00"),
    ]


def test_scheduler_downloads_the_catalog_at_first_start(settings, clock, ran):
    db.migrate(settings)
    clock.now = at("2026-06-15T10:00:00")
    with closing(db.connect(settings)) as conn, conn:  # a success on record, but no file
        conn.execute(
            "INSERT INTO job_runs (job, started_at, finished_at, status) VALUES "
            "('catalog', ?, ?, 'ok')",
            ("2026-06-15T01:00:00+00:00", "2026-06-15T01:00:00+00:00"),
        )
    assert "catalog" in jobs.Scheduler(settings, clock=clock).due()


def test_scheduler_skips_a_job_that_is_running(catalog, clock, ran):
    scheduler = jobs.Scheduler(catalog, clock=clock)
    with ingest._running:
        assert scheduler.due() == ["backup"]
    assert scheduler.due() == ["catalog", "backup"]


def test_scheduler_thread_starts_and_stops(catalog, monkeypatch):
    ticks = []
    monkeypatch.setattr(jobs.Scheduler, "tick", lambda self: ticks.append(1))
    scheduler = jobs.Scheduler(catalog, interval=0.01)
    scheduler.start()
    scheduler.start()  # a second start does nothing
    time.sleep(0.2)
    scheduler.stop()
    scheduler._thread.join(timeout=2)
    assert not scheduler._thread.is_alive() and len(ticks) >= 2
    assert scheduler._thread.daemon


def test_scheduler_survives_a_job_that_raises(catalog, monkeypatch):
    seen = []

    def boom(self):
        seen.append(1)
        raise RuntimeError("no")

    monkeypatch.setattr(jobs.Scheduler, "tick", boom)
    scheduler = jobs.Scheduler(catalog, interval=0.01)
    scheduler.start()
    time.sleep(0.15)
    scheduler.stop()
    scheduler._thread.join(timeout=2)
    assert len(seen) >= 2  # it kept going after the first failure


# --- backup ---------------------------------------------------------------------------


@pytest.fixture
def stocked(catalog):
    with closing(db.connect(catalog)) as conn:
        binder = service.create_location(conn, "Binder", "binder")
        for name, set_code, copies in [("Lightning Bolt", "m10", 3), ("Sol Ring", "c21", 2)]:
            printing = conn.execute(
                "SELECT id FROM cat.printings WHERE name = ? AND set_code = ?", (name, set_code)
            ).fetchone()[0]
            service.add_entry(conn, printing, copies, location_id=binder)
    return catalog


def test_backup_is_a_sound_copy_with_a_csv_beside_it(stocked):
    result = jobs.run_backup(stocked, at("2026-06-15T04:00:00"))
    assert result.status == "ok" and "2 entries" in result.message
    assert result.path.parent == stocked.backup_dir
    assert jobs.BACKUP_FILE.fullmatch(result.path.name) and result.path.name.startswith(
        "collection-"
    )

    with closing(sqlite3.connect(result.path)) as copy, closing(db.connect(stocked)) as live:
        assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        for table in ("entries", "locations", "history"):
            query = f"SELECT * FROM {table} ORDER BY id"
            assert copy.execute(query).fetchall() == [tuple(r) for r in live.execute(query)]
        csv_text = result.path.with_suffix(".csv").read_text(encoding="utf-8-sig")
        assert csv_text == exporters.export_csv(live)
        run = live.execute("SELECT job, status, message, finished_at FROM job_runs").fetchone()
    assert (run["job"], run["status"]) == ("backup", "ok") and run["finished_at"]
    assert result.path.name in run["message"]


def test_backup_of_an_empty_collection_is_skipped(catalog):
    result = jobs.run_backup(catalog)
    assert (result.status, result.message) == ("skipped", "the collection is empty")
    assert jobs.list_backups(catalog) == []
    with closing(db.connect(catalog)) as conn:
        assert conn.execute("SELECT status FROM job_runs").fetchone()[0] == "skipped"


def test_backup_rotates_old_files(stocked):
    stocked.backup_dir.mkdir(parents=True)
    now = at("2026-06-15T04:00:00")
    old = nightly(at("2026-03-10T04:00:00")) + ["pre-import-20260101-000000.db"]
    kept = nightly(at("2026-06-12T04:00:00")) + ["pre-import-20260610-000000.db", "notes.txt"]
    kept += nightly(at("2026-03-20T04:00:00"))  # the newest of March stays as the monthly one
    for name in old + kept:
        (stocked.backup_dir / name).write_text("x")
    result = jobs.run_backup(stocked, now)
    assert "3 old files removed" in result.message
    present = {p.name for p in stocked.backup_dir.iterdir()}
    assert not present & set(old) and set(kept) <= present
    assert len(present) == len(kept) + 2  # plus tonight's .db and .csv


def test_backup_failure_is_recorded_not_raised(stocked):
    blocked = replace(
        stocked, backup_dir=stocked.data_dir / "collection.db"
    )  # a file, not a folder
    result = jobs.run_backup(blocked)
    assert result.status == "error" and result.message
    with closing(db.connect(stocked)) as conn:
        run = conn.execute("SELECT status, message, finished_at FROM job_runs").fetchone()
    assert run["status"] == "error" and run["finished_at"]
    assert not jobs.is_running("backup")


def test_list_backups_newest_first_and_only_ours(stocked):
    stocked.backup_dir.mkdir(parents=True)
    for index, name in enumerate(
        [
            "collection-20260610-040000.db",
            "pre-import-20260611-090000.db",
            "notes.txt",
            "collection.db",
        ]
    ):
        path = stocked.backup_dir / name
        path.write_text("x")
        import os

        os.utime(path, (1_700_000_000 + index, 1_700_000_000 + index))
    assert [p.name for p in jobs.list_backups(stocked)] == [
        "pre-import-20260611-090000.db",
        "collection-20260610-040000.db",
    ]


# --- jobs never overlap ---------------------------------------------------------------


def test_backups_never_overlap(stocked, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    real = jobs._backup

    def slow(conn, settings, now):
        entered.set()
        release.wait(timeout=5)
        return real(conn, settings, now)

    monkeypatch.setattr(jobs, "_backup", slow)
    first: list[jobs.JobResult] = []
    worker = threading.Thread(target=lambda: first.append(jobs.run_backup(stocked)))
    worker.start()
    assert entered.wait(timeout=5) and jobs.is_running("backup")

    second = jobs.run_backup(stocked)  # while the first is still going
    assert second.status == "busy"
    assert jobs.start(stocked, "backup") is False

    release.set()
    worker.join(timeout=5)
    assert first[0].status == "ok" and not jobs.is_running("backup")
    with closing(db.connect(stocked)) as conn:
        assert [r[0] for r in conn.execute("SELECT status FROM job_runs")] == ["ok"]
    assert len([p for p in jobs.list_backups(stocked) if p.suffix == ".db"]) == 1


def test_catalog_updates_never_overlap(catalog):
    with ingest._running:
        assert jobs.is_running("catalog")
        assert jobs.run_catalog(catalog).status == "busy"
        assert jobs.start(catalog, "catalog") is False
    assert not jobs.is_running("catalog")
    with closing(db.connect(catalog)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM job_runs").fetchone()[0] == 0


def test_start_runs_in_the_background(stocked):
    assert jobs.start(stocked, "nonsense") is False
    assert jobs.start(stocked, "backup") is True
    for _ in range(100):
        if not jobs.is_running("backup") and jobs.list_backups(stocked):
            break
        time.sleep(0.05)
    assert len(jobs.list_backups(stocked)) == 2


def test_runs_left_running_by_a_dead_process_become_errors(catalog):
    with closing(db.connect(catalog)) as conn, conn:
        conn.execute(
            "INSERT INTO job_runs (job, started_at, status) VALUES ('catalog', 'x', 'running')"
        )
        conn.execute(
            "INSERT INTO job_runs (job, started_at, finished_at, status) VALUES ('backup', 'x', 'y', 'ok')"
        )
    assert jobs.mark_interrupted(catalog) == 1
    with closing(db.connect(catalog)) as conn:
        rows = [
            tuple(r) for r in conn.execute("SELECT job, status, message FROM job_runs ORDER BY id")
        ]
    assert rows == [("catalog", "error", "interrupted"), ("backup", "ok", None)]


# --- progress of a catalog update -----------------------------------------------------


def test_update_reports_its_phases(settings, monkeypatch):
    phases = []
    real = ingest._step
    monkeypatch.setattr(
        ingest, "_step", lambda phase, total=0: (phases.append(phase), real(phase, total))[1]
    )
    ingest.run_update(settings, from_file=CARDS_JSON, sets_file=SETS_JSON, min_rows=1)
    assert phases == ["building", "publishing", "idle"]
    assert ingest.progress.phase == "idle"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "data.scryfall.io":
            return httpx.Response(200, content=CARDS_JSON.read_bytes())
        if request.url.path == "/bulk-data":
            entry = {
                "type": "default_cards",
                "updated_at": "new",
                "download_uri": "https://data.scryfall.io/x",
            }
            return httpx.Response(200, json={"data": [entry]})
        return httpx.Response(200, json={"data": []})

    phases.clear()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ingest.run_update(settings, client=client, min_rows=1)
    assert phases == ["checking", "downloading", "building", "publishing", "idle"]


# --- settings -------------------------------------------------------------------------


def test_scheduler_setting():
    assert load_settings({}).scheduler is True
    assert load_settings({"MAGICDB_SCHEDULER": "off"}).scheduler is False
    assert load_settings({"MAGICDB_SCHEDULER": "ON"}).scheduler is True
    with pytest.raises(ConfigError, match="MAGICDB_SCHEDULER"):
        load_settings({"MAGICDB_SCHEDULER": "maybe"})


def test_a_real_server_starts_the_scheduler_and_a_test_client_does_not(catalog, monkeypatch):
    monkeypatch.setattr(jobs.Scheduler, "tick", lambda self: [])
    with closing(db.connect(catalog)) as conn, conn:
        conn.execute(
            "INSERT INTO job_runs (job, started_at, status) VALUES ('catalog', 'x', 'running')"
        )

    app = create_app(catalog)
    TestClient(app).get("/healthz")
    assert app.state.scheduler is None  # no lifespan: nothing runs behind a test's back

    with TestClient(app):  # entering runs the startup, as a real server does
        assert app.state.scheduler._thread.is_alive()
        thread = app.state.scheduler._thread
    thread.join(timeout=2)
    assert not thread.is_alive()
    with closing(db.connect(catalog)) as conn:
        assert conn.execute("SELECT status FROM job_runs").fetchone()[0] == "error"

    off = create_app(replace(catalog, scheduler=False))
    with TestClient(off):
        assert off.state.scheduler is None


# --- the administration page ----------------------------------------------------------


@pytest.fixture
def client(stocked, monkeypatch):
    monkeypatch.setitem(jobs.RUNNERS, "catalog", lambda settings: jobs.JobResult("ok", "fake"))
    return TestClient(create_app(stocked), follow_redirects=False)


def wait_until_idle():
    for _ in range(100):
        if not jobs.is_running("backup"):
            return
        time.sleep(0.05)


def test_admin_page(client, fixture_cards):
    page = client.get("/admin")
    assert page.status_code == 200
    for text in [
        "Card catalog",
        f"<dd>{len(fixture_cards)}</dd>",
        "No backups yet.",
        "Nothing has run yet.",
        "Catalog update at 3:00, backup at 4:00, every day.",
        'action="/admin/catalog/run"',
        'action="/admin/backup/run"',
    ]:
        assert text in page.text, text
    assert 'http-equiv="refresh"' not in page.text  # nothing is running
    assert 'href="/admin"' in client.get("/").text  # linked from every page's footer


def test_back_up_now_and_download(client, stocked):
    response = client.post("/admin/backup/run")
    assert (response.status_code, response.headers["location"]) == (303, "/admin?started=yes")
    wait_until_idle()
    page = client.get("/admin?started=yes").text
    assert "Started." in page and "No backups yet." not in page
    database, sheet = sorted(p.name for p in jobs.list_backups(stocked))[::-1]
    assert f'href="/admin/backups/{database}"' in page and f'href="/admin/backups/{sheet}"' in page
    assert ">Backup</td>" in page and ">done</span>" in page  # the run is in the history

    download = client.get(f"/admin/backups/{database}")
    assert download.status_code == 200 and download.content.startswith(b"SQLite format 3")
    assert f'filename="{database}"' in download.headers["content-disposition"]
    assert client.get(f"/admin/backups/{sheet}").content.startswith(b"\xef\xbb\xbfName,")


@pytest.mark.parametrize(
    "name",
    ["collection.db", "..%2Fcollection.db", "collection-20260101-000000.db", "x.db", "notes.txt"],
)
def test_only_backups_can_be_downloaded(client, stocked, name):
    stocked.backup_dir.mkdir(parents=True, exist_ok=True)
    (stocked.backup_dir / "notes.txt").write_text("private")
    assert client.get(f"/admin/backups/{name}").status_code == 404


def test_run_now_when_busy_or_unknown(client):
    with ingest._running:
        busy = client.post("/admin/catalog/run")
        assert busy.headers["location"] == "/admin?started=busy"
        page = client.get("/admin?started=busy").text
        assert "That is already running." in page and "Running now" in page
        assert 'http-equiv="refresh"' in page  # reloads by itself while it runs
        assert "disabled" in page
    assert client.post("/admin/catalog/run").headers["location"] == "/admin?started=yes"
    assert client.post("/admin/nonsense/run").status_code == 404
    assert client.get("/admin/catalog/run").status_code in (404, 405)  # a change is a POST


def test_no_catalog_page_shows_progress(settings):
    client = TestClient(create_app(settings))
    page = client.get("/search?q=bolt")
    assert page.status_code == 503 and 'http-equiv="refresh"' in page.text
    assert "Running now" not in page.text
    with ingest._running:
        ingest._step("downloading", 1000)
        ingest.progress.done = 250
        page = client.get("/search?q=bolt").text
        assert "Running now" in page and "downloading the card data" in page and "25%" in page
        ingest._step("building")
        ingest.progress.done = 5000
        assert "reading the cards" in client.get("/admin").text
        ingest._step("idle")
    assert "has not been downloaded yet" in client.get("/admin").text


def test_admin_page_in_italian(client):
    client.get("/lang/it")
    page = client.get("/admin").text
    assert "Amministrazione" in page and "Fai un backup ora" in page
    assert "Aggiornamento del catalogo alle 3:00" in page


# --- optional password ----------------------------------------------------------------


def basic(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_password_protects_everything_but_healthz(catalog):
    client = TestClient(create_app(replace(catalog, password="s3cret: è")), follow_redirects=False)
    for path in ("/", "/search?q=bolt", "/admin", "/static/app.css", "/export/csv"):
        refused = client.get(path)
        assert refused.status_code == 401, path
        assert refused.headers["www-authenticate"].startswith('Basic realm="magic-db"')
    assert client.post("/locations", data={"name": "x"}).status_code == 401
    assert client.get("/healthz").status_code == 200

    assert client.get("/", headers=basic("anyone", "s3cret: è")).status_code == 200
    assert client.get("/admin", headers=basic("", "s3cret: è")).status_code == 200
    for wrong in [
        basic("anyone", "wrong"),
        basic("s3cret: è", ""),
        {"Authorization": "Basic not-base64!"},
        {"Authorization": "Bearer s3cret"},
        {"Authorization": ""},
    ]:
        assert client.get("/", headers=wrong).status_code == 401
    assert "Password required" in client.get("/admin", headers=basic("a", "s3cret: è")).text


def test_no_password_by_default(catalog):
    assert TestClient(create_app(catalog)).get("/admin").status_code == 200
