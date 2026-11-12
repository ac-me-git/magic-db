"""Work the app does by itself: the nightly catalog update and backup (SPEC §8).

There is no cron and no separate process: a daemon thread wakes once a minute and runs
whatever is due. Every run is recorded in ``job_runs``; a job never overlaps itself.
"""

import logging
import re
import sqlite3
import threading
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from magicdb import db
from magicdb.catalog import ingest, publish
from magicdb.collection import exporters
from magicdb.config import Settings

log = logging.getLogger(__name__)

JOBS = ("catalog", "backup")
STALE = timedelta(hours=36)  # a success older than this is due again, whatever the hour
RETRY = timedelta(hours=1)  # after an attempt, wait this long before trying again
KEEP_DAILY = timedelta(days=7)
KEEP_WEEKLY = timedelta(weeks=4)
KEEP_MONTHS = 12
KEEP_SAFETY = timedelta(days=30)  # pre-import and pre-migration copies

_NIGHTLY = re.compile(r"collection-(\d{8}-\d{6})\.(db|csv)")
_SAFETY = re.compile(r"pre-(?:import|migration)-(\d{8}-\d{6})\.db")
BACKUP_FILE = re.compile(r"(?:collection|pre-import|pre-migration)-\d{8}-\d{6}\.(?:db|csv)")


@dataclass(frozen=True)
class JobResult:
    status: str  # ok | skipped | error | busy
    message: str
    path: Path | None = None


# --- when is a job due ----------------------------------------------------------------


def is_due(
    now: datetime,
    hour: int,
    last_success: datetime | None,
    last_attempt: datetime | None = None,
    running: bool = False,
) -> bool:
    """Should a nightly job run now? All times are local and naive.

    Due when it has not succeeded today and its hour has come, or when its last success
    is more than 36 hours old (the PC was off at night). Never while it is running, and
    not within an hour of the last attempt, so a failing job does not hammer.
    """
    if running:
        return False
    if last_attempt is not None and timedelta(0) <= now - last_attempt < RETRY:
        return False
    if last_success is None or now - last_success > STALE:
        return True
    return now.hour >= hour and last_success.date() < now.date()


def _local(stamp: str | None) -> datetime | None:
    """A UTC timestamp from ``job_runs`` as local naive time."""
    if not stamp:
        return None
    return datetime.fromisoformat(stamp).astimezone().replace(tzinfo=None)


def last_times(conn: sqlite3.Connection, job: str) -> tuple[datetime | None, datetime | None]:
    """``(last success, last attempt)`` of a job. 'skipped' counts as a success: the
    job looked and there was nothing to do."""
    success = conn.execute(
        "SELECT MAX(finished_at) FROM job_runs WHERE job = ? AND status IN ('ok', 'skipped')",
        (job,),
    ).fetchone()[0]
    attempt = conn.execute("SELECT MAX(started_at) FROM job_runs WHERE job = ?", (job,)).fetchone()[
        0
    ]
    return _local(success), _local(attempt)


# --- backup rotation ------------------------------------------------------------------


def _stamp(text: str) -> datetime:
    return datetime.strptime(text, "%Y%m%d-%H%M%S")


def plan_rotation(names: list[str], now: datetime) -> list[str]:
    """Which backup files to delete. A pure function of the file names and the time.

    Nightly backups (``collection-<stamp>.db`` with its ``.csv``): keep every one from
    the last 7 days, the newest of each ISO week of the last 4 weeks, and the newest of
    each month of the last 12 months. Safety copies (``pre-import-…``,
    ``pre-migration-…``) are kept 30 days. Any other file is left alone.
    """
    nightly: dict[datetime, list[str]] = {}
    delete: list[str] = []
    for name in names:
        if match := _NIGHTLY.fullmatch(name):
            nightly.setdefault(_stamp(match.group(1)), []).append(name)
        elif (match := _SAFETY.fullmatch(name)) and now - _stamp(match.group(1)) > KEEP_SAFETY:
            delete.append(name)

    keep: set[datetime] = set()
    newest_of_week: dict[tuple[int, int], datetime] = {}
    newest_of_month: dict[tuple[int, int], datetime] = {}
    for stamp in nightly:
        age = now - stamp
        if age < KEEP_DAILY:
            keep.add(stamp)
        if age < KEEP_WEEKLY:
            week = stamp.isocalendar()[:2]
            newest_of_week[week] = max(stamp, newest_of_week.get(week, stamp))
        months_ago = (now.year - stamp.year) * 12 + now.month - stamp.month
        if months_ago < KEEP_MONTHS:
            month = (stamp.year, stamp.month)
            newest_of_month[month] = max(stamp, newest_of_month.get(month, stamp))
    keep.update(newest_of_week.values(), newest_of_month.values())
    for stamp, files in nightly.items():
        if stamp not in keep:
            delete.extend(files)
    return sorted(delete)


def rotate(settings: Settings, now: datetime) -> list[str]:
    """Delete the backups whose time has passed; return their names."""
    if not settings.backup_dir.exists():
        return []
    names = [path.name for path in settings.backup_dir.iterdir() if path.is_file()]
    removed = []
    for name in plan_rotation(names, now):
        try:
            (settings.backup_dir / name).unlink()
        except OSError:
            continue  # in use or gone: the next run tries again
        removed.append(name)
    return removed


def list_backups(settings: Settings) -> list[Path]:
    """Backup files, newest first."""
    if not settings.backup_dir.exists():
        return []
    files = [p for p in settings.backup_dir.iterdir() if BACKUP_FILE.fullmatch(p.name)]
    return sorted(files, key=lambda p: (p.stat().st_mtime, p.name), reverse=True)


# --- running jobs ---------------------------------------------------------------------

_backup_running = threading.Lock()


def is_running(job: str) -> bool:
    return ingest.is_running() if job == "catalog" else _backup_running.locked()


def _record_start(conn: sqlite3.Connection, job: str) -> int:
    with conn:
        return conn.execute(
            "INSERT INTO job_runs (job, started_at, status) VALUES (?, ?, 'running')",
            (job, db.utcnow()),
        ).lastrowid


def _record_end(conn: sqlite3.Connection, run_id: int, result: JobResult) -> None:
    with conn:
        conn.execute(
            "UPDATE job_runs SET finished_at = ?, status = ?, message = ? WHERE id = ?",
            (db.utcnow(), result.status, result.message, run_id),
        )


def _backup(conn: sqlite3.Connection, settings: Settings, now: datetime) -> JobResult:
    entries = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    locations = conn.execute("SELECT COUNT(*) FROM locations").fetchone()[0]
    if not entries and not locations:
        return JobResult("skipped", "the collection is empty")
    path = db.backup_collection(conn, settings, "collection")
    with closing(sqlite3.connect(path)) as copy:
        verdict = copy.execute("PRAGMA integrity_check").fetchone()[0]
        copied = copy.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    if verdict != "ok" or copied != entries:
        path.unlink(missing_ok=True)
        return JobResult("error", f"the copy is not sound ({verdict}, {copied}/{entries} entries)")
    path.with_suffix(".csv").write_text(
        exporters.export_csv(conn), encoding="utf-8-sig", newline=""
    )
    removed = rotate(settings, now)
    message = f"{path.name}: {entries} entries"
    if removed:
        message += f"; {len(removed)} old files removed"
    return JobResult("ok", message, path)


def run_backup(settings: Settings, now: datetime | None = None) -> JobResult:
    """Back up the collection (database copy + CSV), then rotate. Never raises."""
    if not _backup_running.acquire(blocking=False):
        return JobResult("busy", "a backup is already running")
    try:
        db.migrate(settings)
        with closing(db.connect(settings)) as conn:
            run_id = _record_start(conn, "backup")
            try:
                result = _backup(conn, settings, now or datetime.now())
            except Exception as exc:  # disk full, folder gone: a failed run, not a crash
                log.exception("backup failed")
                result = JobResult("error", f"{type(exc).__name__}: {exc}")
            _record_end(conn, run_id, result)
        return result
    finally:
        _backup_running.release()


def run_catalog(settings: Settings) -> JobResult:
    """Update the card catalog. ``ingest`` records the run itself."""
    result = ingest.run_update(settings)
    if result.status == "skipped" and "already running" in result.message:
        return JobResult("busy", result.message)
    return JobResult(result.status, result.message, result.path)


RUNNERS: dict[str, Callable[[Settings], JobResult]] = {
    "catalog": run_catalog,
    "backup": run_backup,
}


def start(settings: Settings, job: str) -> bool:
    """Run a job in the background, for the "now" buttons. False if it is running."""
    if job not in RUNNERS or is_running(job):
        return False
    threading.Thread(
        target=lambda: RUNNERS[job](settings), name=f"magicdb-{job}", daemon=True
    ).start()
    return True


def mark_interrupted(settings: Settings) -> int:
    """At startup: runs left 'running' by a process that died are errors."""
    db.migrate(settings)
    with closing(db.connect(settings)) as conn, conn:
        return conn.execute(
            "UPDATE job_runs SET status = 'error', message = 'interrupted', finished_at = ? "
            "WHERE status = 'running'",
            (db.utcnow(),),
        ).rowcount


# --- the scheduler --------------------------------------------------------------------


class Scheduler:
    """Wakes once a minute and runs the jobs that are due, one after the other."""

    def __init__(
        self,
        settings: Settings,
        clock: Callable[[], datetime] = datetime.now,
        interval: float = 60,
    ) -> None:
        self.settings = settings
        self.clock = clock
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def due(self) -> list[str]:
        now = self.clock()
        hours = {"catalog": self.settings.update_hour, "backup": self.settings.backup_hour}
        found = []
        with closing(db.connect(self.settings)) as conn:
            for job in JOBS:
                success, attempt = last_times(conn, job)
                if job == "catalog" and publish.live_catalog(self.settings) is None:
                    success = None  # first start: there is nothing to search yet
                if is_due(now, hours[job], success, attempt, is_running(job)):
                    found.append(job)
        return found

    def tick(self) -> list[str]:
        """Run what is due now; return the names of the jobs that ran."""
        ran = []
        for job in self.due():
            result = RUNNERS[job](self.settings)
            if result.status != "busy":
                ran.append(job)
        return ran

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # the scheduler must survive anything a job does
                log.exception("scheduler tick failed")
            self._stop.wait(self.interval)

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="magicdb-jobs", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
