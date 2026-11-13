"""Is there a newer release, and installing it (SPEC §7, Administration).

The check asks GitHub for the latest release and compares version numbers. Installing is
only possible for the Windows executable, which can replace its own file: a running
program cannot be overwritten on Windows, but it can be renamed. So the new program is
downloaded beside the old one, checked against the digest GitHub publishes for it, the
two are swapped by renaming, and a new process is started before this one exits.

A Docker container or a source checkout cannot update itself; the page says how to.
"""

import hashlib
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from magicdb import __version__
from magicdb.config import DEFAULT_RELEASES_URL, Settings

ASSET_NAME = "magic-db.exe"
CHECK_EVERY = 12 * 3600  # seconds a good answer is trusted
RETRY_AFTER = 10 * 60  # seconds before asking again after a failure
CHECK_TIMEOUT = 5.0  # the administration page waits for this at most
DOWNLOAD_TIMEOUT = 30.0  # per read, not for the whole file
MAX_SIZE = 200 * 1024 * 1024  # refuse a download that is absurdly large
NOTES_LIMIT = 2000
EXIT_DELAY = 1.5  # seconds: let the answer reach the browser before exiting
AFTER_UPDATE = "MAGICDB_AFTER_UPDATE"  # set for the process started by an update
HEADERS = {
    "User-Agent": f"magic-db/{__version__}",
    "Accept": "application/vnd.github+json",
}

_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
_DIGEST = re.compile(r"sha256:([0-9a-f]{64})")


# The two calls that reach the network, by name so that tests can replace them.
_get = httpx.get
_stream = httpx.stream


class UpdateError(Exception):
    """The update could not be installed. Nothing has been changed."""


@dataclass
class Release:
    version: str
    page: str  # the release page, for people
    notes: str
    asset_url: str = ""
    asset_digest: str = ""  # hex sha256, empty when GitHub gives none
    asset_size: int = 0


@dataclass
class State:
    checked_at: float = 0.0  # time.time() of the last attempt; 0 = never
    release: Release | None = None
    error: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


state = State()


def parse_version(text: str) -> tuple[int, int, int] | None:
    """'v1.2.3' or '1.2.3' as numbers; None for anything else (pre-releases included)."""
    match = _VERSION.fullmatch(text.strip())
    return tuple(int(part) for part in match.groups()) if match else None  # type: ignore[return-value]


def is_newer(candidate: str, current: str = __version__) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    return new is not None and old is not None and new > old


def _on_windows() -> bool:
    """By name, so that tests can pretend without changing ``os.name`` for everyone."""
    return os.name == "nt"


def install_kind() -> str:
    """How this copy was installed: 'exe', 'docker' or 'source'."""
    if getattr(sys, "frozen", False):
        return "exe" if _on_windows() else "source"
    return "docker" if Path("/.dockerenv").exists() else "source"


def _safe_page(url: str) -> str:
    return url if urlsplit(url).scheme in ("http", "https") else ""


def read_release(payload: object) -> Release:
    """The parts of GitHub's 'latest release' answer that are used."""
    if not isinstance(payload, dict) or parse_version(str(payload.get("tag_name", ""))) is None:
        raise ValueError("unexpected answer")
    release = Release(
        version=str(payload["tag_name"]).strip().removeprefix("v"),
        page=_safe_page(str(payload.get("html_url", ""))),
        notes=str(payload.get("body") or "").strip()[:NOTES_LIMIT],
    )
    for asset in payload.get("assets") or []:
        if isinstance(asset, dict) and asset.get("name") == ASSET_NAME:
            digest = _DIGEST.fullmatch(str(asset.get("digest") or ""))
            release.asset_url = str(asset.get("browser_download_url") or "")
            release.asset_digest = digest.group(1) if digest else ""
            release.asset_size = int(asset.get("size") or 0)
    return release


def check(settings: Settings, force: bool = False, now: float | None = None) -> State:
    """Ask for the latest release, unless a recent answer is still good."""
    if not settings.update_check:
        return state
    now = time.time() if now is None else now
    with state.lock:
        wait = RETRY_AFTER if state.error else CHECK_EVERY
        if not force and state.checked_at and now - state.checked_at < wait:
            return state
        state.checked_at = now
        try:
            response = _get(
                settings.releases_url,
                headers=HEADERS,
                timeout=CHECK_TIMEOUT,
                follow_redirects=True,
            )
            response.raise_for_status()
            state.release, state.error = read_release(response.json()), ""
        except httpx.HTTPStatusError as error:
            state.error = f"HTTP {error.response.status_code}"
        except (httpx.HTTPError, ValueError) as error:
            state.error = type(error).__name__ if not str(error) else str(error)[:200]
    return state


def available(settings: Settings) -> Release | None:
    """The release to offer: the known latest one, if newer than this program."""
    release = state.release
    if settings.update_check and release is not None and is_newer(release.version):
        return release
    return None


def _allowed_hosts(settings: Settings) -> set[str]:
    """Where the program may be downloaded from: GitHub, or the host that was asked
    for the release when MAGICDB_RELEASES_URL points somewhere else."""
    hosts = {"github.com"}
    if settings.releases_url != DEFAULT_RELEASES_URL:
        hosts.add(urlsplit(settings.releases_url).hostname or "")
    return hosts


def can_install(settings: Settings, release: Release | None) -> bool:
    return (
        release is not None
        and install_kind() == "exe"
        and bool(release.asset_digest)
        and urlsplit(release.asset_url).hostname in _allowed_hosts(settings)
    )


def download(release: Release, target: Path) -> None:
    """Download the release's program to ``target`` and check size and digest."""
    digest = hashlib.sha256()
    size = 0
    try:
        with (
            _stream(
                "GET",
                release.asset_url,
                headers={"User-Agent": HEADERS["User-Agent"]},
                timeout=DOWNLOAD_TIMEOUT,
                follow_redirects=True,
            ) as response,
            target.open("wb") as out,
        ):
            response.raise_for_status()
            for chunk in response.iter_bytes(1024 * 1024):
                size += len(chunk)
                if size > MAX_SIZE:
                    raise UpdateError("the download is larger than expected.")
                digest.update(chunk)
                out.write(chunk)
    except (httpx.HTTPError, OSError) as error:
        target.unlink(missing_ok=True)
        raise UpdateError(f"the download failed ({error}).") from error
    except UpdateError:
        target.unlink(missing_ok=True)
        raise
    if (release.asset_size and size != release.asset_size) or (
        digest.hexdigest() != release.asset_digest
    ):
        target.unlink(missing_ok=True)
        raise UpdateError("the downloaded file is not the one that was published.")


def swap(program: Path, new: Path) -> Path:
    """Put ``new`` in the place of ``program``; the old one is kept as ``.old``."""
    old = program.with_name(program.name + ".old")
    try:
        old.unlink(missing_ok=True)
        os.replace(program, old)
    except OSError as error:
        new.unlink(missing_ok=True)
        raise UpdateError(f"the program file could not be replaced ({error}).") from error
    try:
        os.replace(new, program)
    except OSError as error:
        os.replace(old, program)  # put the running program back where it was
        new.unlink(missing_ok=True)
        raise UpdateError(f"the program file could not be replaced ({error}).") from error
    return old


def restart_command(program: Path, argv: list[str]) -> list[str]:
    """Start again with the same arguments, without opening a second browser tab."""
    return [str(program), *[arg for arg in argv if arg != "--browser"], "--no-browser"]


def _spawn(command: list[str], folder: Path) -> None:
    env = dict(os.environ)
    env[AFTER_UPDATE] = "1"
    # the new program must unpack itself afresh, not reuse this one's unpacked files
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen(  # noqa: S603 - our own program, at the path it was started from
        command,
        cwd=folder,
        env=env,
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
    )


def _exit_soon() -> None:
    timer = threading.Timer(EXIT_DELAY, os._exit, [0])
    timer.daemon = True
    timer.start()


def install(settings: Settings, spawn=None, leave=None) -> Release:
    """Download the available release, swap it in and restart. Windows executable only."""
    release = available(settings)
    if release is None:
        raise UpdateError("there is no newer version.")
    if not can_install(settings, release):
        raise UpdateError("this version cannot be installed from here.")
    program = Path(sys.executable).resolve()
    new = program.with_name(program.name + ".new")
    with state.lock:
        download(release, new)
        swap(program, new)
        try:
            (spawn or _spawn)(restart_command(program, sys.argv[1:]), program.parent)
        except OSError as error:
            os.replace(program, new)  # undo the swap: keep running the old program
            os.replace(program.with_name(program.name + ".old"), program)
            new.unlink(missing_ok=True)
            raise UpdateError(f"the new program could not be started ({error}).") from error
    (leave or _exit_soon)()
    return release


def clean_up(program: Path) -> None:
    """Remove what an earlier update left beside the program."""
    for suffix in (".old", ".new"):
        try:
            program.with_name(program.name + suffix).unlink(missing_ok=True)
        except OSError:
            pass  # still in use by the program that is closing: next start gets it


def wait_for_port(host: str, port: int, seconds: float = 30.0) -> None:
    """After an update the previous program may still hold the port for a moment."""
    import socket

    deadline = time.monotonic() + seconds
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    while time.monotonic() < deadline:
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, port))
                return
            except OSError:
                time.sleep(0.5)
