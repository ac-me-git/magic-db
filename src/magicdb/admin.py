"""The administration page: catalog and backup status, run a job now (SPEC §7)."""

import sqlite3
from datetime import datetime
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from magicdb import __version__, db, jobs, selfupdate, web
from magicdb.catalog import ingest, publish

router = APIRouter()
Conn = Annotated[sqlite3.Connection, Depends(db.get_conn)]

HISTORY = 20
SHOWN_BACKUPS = 30


def local_time(stamp: str) -> str:
    """A UTC timestamp as the server's local time, for people: '2026-11-12 07:34'."""
    try:
        return datetime.fromisoformat(stamp).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return stamp


def catalog_status(request: Request, conn: sqlite3.Connection) -> dict[str, object]:
    live = publish.live_catalog(request.app.state.settings)
    status: dict[str, object] = {"running": jobs.is_running("catalog")}
    if live is not None and db.has_catalog(conn):
        meta = dict(conn.execute("SELECT key, value FROM cat.meta"))
        status.update(
            file=live.name,
            size=live.stat().st_size,
            printings=int(meta.get("row_count", 0)),
            scryfall_date=local_time(meta.get("bulk_updated_at", "")),
            built_at=local_time(meta.get("built_at", "")),
        )
    return status


@router.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, conn: Conn, started: str = "", failed: str = "") -> HTMLResponse:
    settings = request.app.state.settings
    checked = selfupdate.check(settings)
    release = selfupdate.available(settings)
    backups = jobs.list_backups(settings)
    return web.render(
        request,
        "admin.html",
        catalog=catalog_status(request, conn),
        progress=ingest.progress,
        backup_running=jobs.is_running("backup"),
        backups=[
            {
                "name": path.name,
                "size": path.stat().st_size,
                "date": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            }
            for path in backups[:SHOWN_BACKUPS]
        ],
        more_backups=max(0, len(backups) - SHOWN_BACKUPS),
        runs=[
            {**dict(run), "started": local_time(run["started_at"])}
            for run in conn.execute(
                "SELECT job, started_at, status, message FROM job_runs ORDER BY id DESC LIMIT ?",
                (HISTORY,),
            )
        ],
        settings=settings,
        data_dir=settings.data_dir.resolve(),
        backup_dir=settings.backup_dir.resolve(),
        version=__version__,
        revision=selfupdate.development_revision()[:7],
        edge=checked.edge,
        edge_date=local_time(checked.edge.date) if checked.edge and checked.edge.date else "",
        newer_edge=selfupdate.newer_edge(settings),
        release=release,
        latest=checked.release.version if checked.release else "",
        check_error=checked.error,
        checked_at=(
            datetime.fromtimestamp(checked.checked_at).strftime("%Y-%m-%d %H:%M")
            if checked.checked_at
            else ""
        ),
        install_kind=selfupdate.install_kind(),
        can_install=selfupdate.can_install(settings, release),
        install_failed=failed[:300],
        started=started if started in ("yes", "busy") else "",
    )


@router.post("/admin/version/check")
def check_version(request: Request) -> Response:
    selfupdate.check(request.app.state.settings, force=True)
    return RedirectResponse("/admin#version", status_code=303)


@router.post("/admin/version/install")
def install_version(request: Request) -> Response:
    """Replace the program with the latest release and restart (Windows executable)."""
    try:
        release = selfupdate.install(request.app.state.settings)
    except selfupdate.UpdateError as error:
        return RedirectResponse(f"/admin?{urlencode({'failed': str(error)})}", status_code=303)
    return web.render(request, "restarting.html", version=release.version)


@router.post("/admin/{job}/run")
def run_now(request: Request, job: str) -> Response:
    if job not in jobs.JOBS:
        return web.render(request, "not_found.html", 404)
    started = jobs.start(request.app.state.settings, job)
    return RedirectResponse(f"/admin?started={'yes' if started else 'busy'}", status_code=303)


@router.get("/admin/backups/{name}")
def download_backup(request: Request, name: str) -> Response:
    """Download a backup. Only names the app itself gives to backups are served."""
    path = request.app.state.settings.backup_dir / name
    if not jobs.BACKUP_FILE.fullmatch(name) or not path.is_file():
        return web.render(request, "not_found.html", 404)
    media = "text/csv" if name.endswith(".csv") else "application/vnd.sqlite3"
    return FileResponse(
        path, media_type=media, filename=name, headers={"Cache-Control": "no-store"}
    )
