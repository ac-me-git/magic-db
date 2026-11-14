"""Checking for a newer release and installing it (SPEC §7, Administration)."""

import hashlib
import sys
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from magicdb import __version__, cli, selfupdate
from magicdb import admin as admin_page
from magicdb.app import create_app
from magicdb.config import DEFAULT_RELEASES_URL, ConfigError, load_settings

NEW_PROGRAM = b"MZ the new program" * 1000
DIGEST = hashlib.sha256(NEW_PROGRAM).hexdigest()
ASSET = "https://github.com/ac-me-git/magic-db/releases/download/v9.9.9/magic-db.exe"


def github(version: str = "9.9.9", **asset: object) -> dict:
    return {
        "tag_name": f"v{version}",
        "html_url": f"https://github.com/ac-me-git/magic-db/releases/tag/v{version}",
        "body": "## What's new\n- everything",
        "assets": [
            {"name": "notes.txt", "browser_download_url": "https://github.com/x"},
            {
                "name": "magic-db.exe",
                "browser_download_url": ASSET,
                "digest": f"sha256:{DIGEST}",
                "size": len(NEW_PROGRAM),
                **asset,
            },
        ],
    }


NEWER = "9bc60dc0ffee0123456789abcdef0123456789ab"
EDGE_URL = (
    "https://api.github.com/repos/ac-me-git/magic-db/actions/workflows/ci.yml/runs"
    "?branch=main&event=push&status=success&per_page=1"
)


def runs(commit: str = NEWER, **head_commit: object) -> dict:
    """GitHub's list of workflow runs, cut down to what is read."""
    return {
        "total_count": 1,
        "workflow_runs": [
            {
                "head_sha": commit,
                "conclusion": "success",
                "head_commit": {
                    "id": commit,
                    "message": "Admin: say more\n\nA longer explanation.",
                    "timestamp": "2026-11-14T10:00:00Z",
                    **head_commit,
                },
            }
        ],
    }


class Answer:
    def __init__(self, payload: object, status: int = 200):
        self.payload, self.status_code = payload, status

    def json(self) -> object:
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=None, response=self)  # type: ignore[arg-type]


class Asked(list):
    """The URLs that were asked; ``answer["value"]`` is what GitHub says next."""

    answer: dict


@pytest.fixture
def latest(monkeypatch):
    """Make GitHub answer with a release; returns the list of URLs that were asked."""
    asked = Asked()
    answer = {"value": Answer(github()), "edge": Answer(runs())}

    def get(url: str, **kwargs: object) -> Answer:
        asked.append(url)
        reply = answer["edge" if "/actions/" in url else "value"]
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(selfupdate, "_get", get)
    asked.answer = answer
    return asked


@pytest.fixture
def downloads(monkeypatch):
    """Serve NEW_PROGRAM (or what the test puts in 'body') for any download."""
    served = {"body": NEW_PROGRAM, "urls": []}

    class Stream:
        def raise_for_status(self) -> None:
            pass

        def iter_bytes(self, size: int):
            body = served["body"]
            yield from (body[i : i + size] for i in range(0, len(body), size))

    @contextmanager
    def stream(method: str, url: str, **kwargs: object):
        served["urls"].append(url)
        yield Stream()

    monkeypatch.setattr(selfupdate, "_stream", stream)
    return served


@pytest.fixture
def as_exe(monkeypatch, tmp_path):
    """Pretend to be the Windows executable, living in a folder of its own."""
    program = tmp_path / "app" / "magic-db.exe"
    program.parent.mkdir()
    program.write_bytes(b"the old program")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(program))
    monkeypatch.setattr(sys, "argv", [str(program)])
    monkeypatch.setattr(selfupdate, "_on_windows", lambda: True)
    return program


# --- versions -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.2.3", (1, 2, 3)),
        ("v1.2.3", (1, 2, 3)),
        (" v10.0.12 ", (10, 0, 12)),
        ("1.2", None),
        ("v1.2.3-rc1", None),
        ("latest", None),
        ("", None),
    ],
)
def test_parse_version(text, expected):
    assert selfupdate.parse_version(text) == expected


def test_is_newer_compares_numbers_not_text():
    assert selfupdate.is_newer("1.10.0", "1.9.0")
    assert selfupdate.is_newer("v2.0.0", "1.99.99")
    assert not selfupdate.is_newer("1.0.0", "1.0.0")
    assert not selfupdate.is_newer("0.9.9", "1.0.0")
    assert not selfupdate.is_newer("2.0.0-beta", "1.0.0")  # pre-releases are not offered
    assert not selfupdate.is_newer(__version__)


def test_read_release():
    release = selfupdate.read_release(github())
    assert release.version == "9.9.9" and release.page.endswith("/v9.9.9")
    assert release.asset_url == ASSET and release.asset_digest == DIGEST
    assert release.asset_size == len(NEW_PROGRAM)

    bare = selfupdate.read_release({"tag_name": "v2.0.0", "html_url": "javascript:alert(1)"})
    assert bare.asset_url == "" and bare.page == ""  # only web links are shown

    no_digest = selfupdate.read_release(github(digest=None))
    assert no_digest.asset_url == ASSET and no_digest.asset_digest == ""

    for junk in (None, [], {}, {"tag_name": "nightly"}, {"message": "Not Found"}):
        with pytest.raises(ValueError):
            selfupdate.read_release(junk)


# --- checking -------------------------------------------------------------------------


def test_check_is_cached_and_can_be_forced(settings, latest):
    assert selfupdate.check(settings, now=1000.0).release.version == "9.9.9"
    assert latest == [DEFAULT_RELEASES_URL]
    selfupdate.check(settings, now=1000.0 + selfupdate.CHECK_EVERY - 1)
    assert len(latest) == 1  # the answer is still good
    selfupdate.check(settings, now=1000.0 + selfupdate.CHECK_EVERY + 1)
    assert len(latest) == 2
    selfupdate.check(settings, force=True, now=1000.0 + selfupdate.CHECK_EVERY + 2)
    assert len(latest) == 3
    assert selfupdate.available(settings).version == "9.9.9"


def test_check_failure_is_reported_and_retried_sooner(settings, latest):
    latest.answer["value"] = httpx.ConnectError("no network")
    checked = selfupdate.check(settings, now=1000.0)
    assert checked.release is None and "no network" in checked.error
    selfupdate.check(settings, now=1000.0 + selfupdate.RETRY_AFTER - 1)
    assert len(latest) == 1
    latest.answer["value"] = Answer({"message": "rate limited"}, status=403)
    assert selfupdate.check(settings, now=1000.0 + selfupdate.RETRY_AFTER + 1).error == "HTTP 403"
    latest.answer["value"] = Answer({"message": "Not Found"})
    assert selfupdate.check(settings, force=True, now=3000.0).error == "unexpected answer"
    latest.answer["value"] = Answer(github())
    recovered = selfupdate.check(settings, force=True, now=4000.0)
    assert recovered.error == "" and recovered.release.version == "9.9.9"


def test_a_failed_check_keeps_the_last_known_release(settings, latest):
    selfupdate.check(settings, now=1000.0)
    latest.answer["value"] = httpx.ConnectError("no network")
    checked = selfupdate.check(settings, force=True, now=2000.0)
    assert checked.error and checked.release.version == "9.9.9"


def test_check_can_be_switched_off(settings, latest):
    off = replace(settings, update_check=False)
    assert selfupdate.check(off, force=True).release is None
    assert latest == [] and selfupdate.available(off) is None


def test_same_or_older_release_is_not_offered(settings, latest):
    latest.answer["value"] = Answer(github(__version__))
    selfupdate.check(settings)
    assert selfupdate.state.release.version == __version__
    assert selfupdate.available(settings) is None


def test_settings():
    assert load_settings({}).update_check is True
    assert load_settings({}).releases_url == DEFAULT_RELEASES_URL
    custom = load_settings(
        {"MAGICDB_UPDATE_CHECK": "off", "MAGICDB_RELEASES_URL": " http://127.0.0.1:9/latest "}
    )
    assert custom.update_check is False and custom.releases_url == "http://127.0.0.1:9/latest"
    with pytest.raises(ConfigError):
        load_settings({"MAGICDB_UPDATE_CHECK": "maybe"})


# --- installing -----------------------------------------------------------------------


def test_install_kind(monkeypatch, as_exe):
    assert selfupdate.install_kind() == "exe"
    monkeypatch.setattr(selfupdate, "_on_windows", lambda: False)
    assert selfupdate.install_kind() == "source"
    monkeypatch.setattr(sys, "frozen", False)
    assert selfupdate.install_kind() in ("source", "docker")


def test_can_install_only_a_checked_program_from_github(settings, as_exe, monkeypatch):
    release = selfupdate.read_release(github())
    assert selfupdate.can_install(settings, release)
    assert not selfupdate.can_install(settings, None)
    assert not selfupdate.can_install(settings, replace(release, asset_digest=""))
    assert not selfupdate.can_install(settings, replace(release, asset_url=""))
    elsewhere = replace(release, asset_url="https://evil.example/magic-db.exe")
    assert not selfupdate.can_install(settings, elsewhere)
    lookalike = replace(release, asset_url="https://github.com.evil.example/magic-db.exe")
    assert not selfupdate.can_install(settings, lookalike)

    # a releases URL of one's own may serve the program too (forks, tests)
    own = replace(settings, releases_url="http://127.0.0.1:8123/latest")
    local = replace(release, asset_url="http://127.0.0.1:8123/magic-db.exe")
    assert selfupdate.can_install(own, local) and not selfupdate.can_install(settings, local)

    monkeypatch.setattr(sys, "frozen", False)
    assert not selfupdate.can_install(settings, release)  # not the executable


def test_install_swaps_the_program_and_restarts(settings, latest, downloads, as_exe):
    selfupdate.check(settings)
    started, left = [], []
    release = selfupdate.install(
        settings,
        spawn=lambda command, folder: started.append((command, folder)),
        leave=lambda: left.append(True),
    )
    assert release.version == "9.9.9" and downloads["urls"] == [ASSET]
    assert as_exe.read_bytes() == NEW_PROGRAM
    assert as_exe.with_name("magic-db.exe.old").read_bytes() == b"the old program"
    assert not as_exe.with_name("magic-db.exe.new").exists()
    assert started == [([str(as_exe), "--no-browser"], as_exe.parent)]
    assert len(left) == 1  # this process is told to exit, once


@pytest.mark.parametrize(
    "body", [NEW_PROGRAM + b"x", NEW_PROGRAM[:-1] + b"!", b"", b"<html>Not found</html>"]
)
def test_a_wrong_download_changes_nothing(settings, latest, downloads, as_exe, body):
    downloads["body"] = body
    selfupdate.check(settings)
    started = []
    with pytest.raises(selfupdate.UpdateError, match="not the one that was published"):
        selfupdate.install(settings, spawn=lambda *a: started.append(a), leave=lambda: 1 / 0)
    assert as_exe.read_bytes() == b"the old program"
    assert sorted(path.name for path in as_exe.parent.iterdir()) == ["magic-db.exe"]
    assert started == []


def test_an_oversized_download_is_refused(settings, latest, downloads, as_exe, monkeypatch):
    monkeypatch.setattr(selfupdate, "MAX_SIZE", 100)
    selfupdate.check(settings)
    with pytest.raises(selfupdate.UpdateError, match="larger than expected"):
        selfupdate.install(settings, spawn=lambda *a: None, leave=lambda: 1 / 0)
    assert sorted(path.name for path in as_exe.parent.iterdir()) == ["magic-db.exe"]


def test_a_failed_download_changes_nothing(settings, latest, as_exe):
    selfupdate.check(settings)  # httpx.stream is offline (conftest)
    with pytest.raises(selfupdate.UpdateError, match="download failed"):
        selfupdate.install(settings, spawn=lambda *a: None, leave=lambda: 1 / 0)
    assert sorted(path.name for path in as_exe.parent.iterdir()) == ["magic-db.exe"]


def test_if_the_new_program_does_not_start_the_old_one_stays(settings, latest, downloads, as_exe):
    selfupdate.check(settings)

    def broken(command, folder):
        raise OSError("not a valid application")

    with pytest.raises(selfupdate.UpdateError, match="could not be started"):
        selfupdate.install(settings, spawn=broken, leave=lambda: 1 / 0)
    assert as_exe.read_bytes() == b"the old program"
    assert sorted(path.name for path in as_exe.parent.iterdir()) == ["magic-db.exe"]


def test_install_refuses_without_a_newer_release_or_outside_the_exe(
    settings, latest, downloads, as_exe, monkeypatch
):
    with pytest.raises(selfupdate.UpdateError, match="no newer version"):
        selfupdate.install(settings, spawn=lambda *a: None, leave=lambda: 1 / 0)
    selfupdate.check(settings)
    monkeypatch.setattr(sys, "frozen", False)
    with pytest.raises(selfupdate.UpdateError, match="cannot be installed from here"):
        selfupdate.install(settings, spawn=lambda *a: None, leave=lambda: 1 / 0)
    assert downloads["urls"] == [] and as_exe.read_bytes() == b"the old program"


def test_restart_command_keeps_arguments_and_never_opens_the_browser():
    program = Path("C:/app/magic-db.exe")
    assert selfupdate.restart_command(program, []) == [str(program), "--no-browser"]
    assert selfupdate.restart_command(program, ["serve", "--browser"]) == [
        str(program),
        "serve",
        "--no-browser",
    ]


def test_clean_up_removes_leftovers(tmp_path):
    program = tmp_path / "magic-db.exe"
    for name in ("magic-db.exe", "magic-db.exe.old", "magic-db.exe.new", "data.txt"):
        (tmp_path / name).write_text("x")
    selfupdate.clean_up(program)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["data.txt", "magic-db.exe"]
    selfupdate.clean_up(program)  # nothing to remove is fine


def test_wait_for_port_returns_once_free():
    import socket

    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        port = holder.getsockname()[1]
    selfupdate.wait_for_port("127.0.0.1", port, seconds=2)  # free: returns at once


def test_the_executable_cleans_up_and_waits_at_start(settings, as_exe, monkeypatch):
    as_exe.with_name("magic-db.exe.old").write_text("previous")
    waited, served = [], []
    monkeypatch.setenv(selfupdate.AFTER_UPDATE, "1")
    monkeypatch.setattr(selfupdate, "wait_for_port", lambda host, port: waited.append(port))
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: served.append(kwargs))
    assert cli._serve(settings, open_browser=False) == 0
    assert not as_exe.with_name("magic-db.exe.old").exists()
    assert waited == [settings.port] and served == [{"host": "127.0.0.1", "port": settings.port}]


# --- the administration page ----------------------------------------------------------


@pytest.fixture
def admin(catalog):
    return TestClient(create_app(catalog), follow_redirects=False)


def test_admin_shows_a_new_version_with_how_to_update_from_source(admin, latest):
    page = admin.get("/admin").text
    assert "Software version" in page and f"<dd>{__version__}</dd>" in page
    assert "Version 9.9.9 is available." in page
    assert 'href="https://github.com/ac-me-git/magic-db/releases/tag/v9.9.9"' in page
    assert "git pull" in page and "/admin/version/install" not in page
    assert len(latest) == 1
    admin.get("/admin")
    assert len(latest) == 1  # opening the page again does not ask again


def test_admin_offers_one_click_install_to_the_executable(admin, latest, as_exe):
    page = admin.get("/admin").text
    assert 'action="/admin/version/install"' in page
    assert "Install version 9.9.9 and restart" in page and "git pull" not in page


def test_admin_docker_instructions(admin, latest, monkeypatch):
    monkeypatch.setattr(selfupdate, "install_kind", lambda: "docker")
    page = admin.get("/admin").text
    assert "docker compose pull" in page and "/admin/version/install" not in page


def test_admin_when_up_to_date_offline_or_switched_off(admin, latest, catalog):
    latest.answer["value"] = Answer(github(__version__))
    page = admin.get("/admin").text
    assert "You have the latest version." in page and "is available" not in page

    latest.answer["value"] = httpx.ConnectError("no network")
    assert admin.post("/admin/version/check").headers["location"] == "/admin#version"
    page = admin.get("/admin").text
    assert "Could not check for a new version: no network" in page
    assert admin.get("/admin").status_code == 200

    off = TestClient(create_app(replace(catalog, update_check=False)))
    page = off.get("/admin").text
    assert "switched off (MAGICDB_UPDATE_CHECK=off)" in page
    assert "/admin/version/check" not in page and "Latest released" not in page


def test_check_now_asks_again(admin, latest):
    admin.get("/admin")
    admin.post("/admin/version/check")
    assert len(latest) == 2
    assert admin.get("/admin/version/check").status_code in (404, 405)  # a change is a POST


def test_install_from_the_page(admin, latest, downloads, as_exe, monkeypatch):
    started, left = [], []
    monkeypatch.setattr(selfupdate, "_spawn", lambda command, folder: started.append(command))
    monkeypatch.setattr(selfupdate, "_exit_soon", lambda: left.append(True))
    admin.get("/admin")
    page = admin.post("/admin/version/install")
    assert page.status_code == 200 and "Version 9.9.9 is installed. Restarting" in page.text
    assert 'data-wait-restart="/admin"' in page.text
    assert as_exe.read_bytes() == NEW_PROGRAM and started and left == [True]


def test_install_failure_is_explained_on_the_page(admin, latest, as_exe):
    admin.get("/admin")
    answer = admin.post("/admin/version/install")  # the download is offline (conftest)
    assert answer.status_code == 303 and answer.headers["location"].startswith("/admin?failed=")
    page = admin.get(answer.headers["location"]).text
    assert "The update was not installed: the download failed" in page
    assert "Nothing was changed." in page and as_exe.read_bytes() == b"the old program"


def test_install_is_refused_from_another_site_and_in_italian(admin, latest, as_exe):
    cross = admin.post("/admin/version/install", headers={"Origin": "https://evil.example"})
    assert cross.status_code == 403
    admin.get("/lang/it")
    page = admin.get("/admin").text
    assert "Versione del programma" in page and "È disponibile la versione 9.9.9." in page
    assert "Installa la versione 9.9.9 e riavvia" in page


# --- the development version (the ":edge" image) ----------------------------------------

COMMIT = "199f1a1c0ffee0123456789abcdef0123456789a"


@pytest.fixture
def revision_file(monkeypatch, tmp_path):
    """Where a development image keeps the commit it was built from."""
    path = tmp_path / "REVISION"
    monkeypatch.setattr(selfupdate, "REVISION_FILE", path)
    return path


def test_a_release_or_a_checkout_is_not_a_development_version(revision_file):
    assert selfupdate.development_revision() == ""
    # and the repository never carries the file: only the image's build writes it
    assert not (Path(selfupdate.__file__).parent / "REVISION").exists()


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        (COMMIT + "\n", COMMIT),
        ("  199f1a1  ", "199f1a1"),
        ("", ""),
        ("\n", ""),
        ("main", ""),
        ("199F1A1", ""),
        ("abc123", ""),  # too short to be a commit
        (COMMIT + "0", ""),
        ("<script>alert(1)</script>", ""),
        ("199f1a1\nsecond line", ""),
        ("versión", ""),
    ],
)
def test_development_revision_is_a_commit_or_nothing(revision_file, written, expected):
    revision_file.write_text(written, encoding="utf-8")
    assert selfupdate.development_revision() == expected


def test_admin_says_when_it_is_the_development_version(client, latest, revision_file):
    page = client.get("/admin").text  # no catalog yet: the page must answer all the same
    assert "development version" not in page and f"<dd>{__version__}</dd>" in page

    revision_file.write_text(COMMIT + "\n", encoding="ascii")
    page = client.get("/admin").text
    assert f"<dd>{__version__} · <strong>development version, commit 199f1a1</strong></dd>" in page
    assert f"<dd>{__version__} · development version, commit 199f1a1</dd>" in page  # settings
    assert "This is the development version (the edge image)" in page
    assert f"after version {__version__} that are not released yet" in page
    assert COMMIT[:8] not in page  # the short form only
    assert "Version 9.9.9 is available." in page  # releases are still announced

    client.get("/lang/it")
    page = client.get("/admin").text
    assert "<strong>versione di sviluppo, commit 199f1a1</strong>" in page
    assert "Questa è la versione di sviluppo" in page and "immagine edge" in page


def test_edge_url_follows_the_releases_address(settings):
    assert selfupdate.edge_url(settings) == EDGE_URL
    fork = replace(settings, releases_url="https://api.github.com/repos/me/fork/releases/latest")
    assert selfupdate.edge_url(fork).startswith(
        "https://api.github.com/repos/me/fork/actions/workflows/ci.yml/runs?branch=main"
    )
    for other in ("https://example.com/latest.json", "https://x/releases/latest/extra", ""):
        assert selfupdate.edge_url(replace(settings, releases_url=other)) == ""


def test_read_edge():
    edge = selfupdate.read_edge(runs())
    assert (edge.commit, edge.date, edge.title) == (
        NEWER,
        "2026-11-14T10:00:00Z",
        "Admin: say more",
    )
    assert selfupdate.read_edge({"total_count": 0, "workflow_runs": []}) is None
    bare = selfupdate.read_edge({"workflow_runs": [{"head_sha": NEWER}]})
    assert (bare.commit, bare.date, bare.title) == (NEWER, "", "")
    long = selfupdate.read_edge(runs(message="x" * 500, timestamp="y" * 500))
    assert len(long.title) == selfupdate.TITLE_LIMIT and len(long.date) == 40
    for bad in (
        None,
        [],
        {},
        {"message": "Not Found"},
        {"workflow_runs": "x"},
        {"workflow_runs": ["x"]},
        {"workflow_runs": [{"head_sha": "main"}]},
        {"workflow_runs": [{"head_sha": NEWER[:7]}]},
        {"workflow_runs": [{"head_sha": "<script>" + NEWER}]},
    ):
        with pytest.raises(ValueError):
            selfupdate.read_edge(bad)


def test_only_a_development_version_asks_for_the_newest_one(settings, latest, revision_file):
    selfupdate.check(settings, now=1000.0)
    assert latest == [DEFAULT_RELEASES_URL] and selfupdate.state.edge is None

    revision_file.write_text(COMMIT, encoding="ascii")
    checked = selfupdate.check(settings, force=True, now=2000.0)
    assert latest[1:] == [DEFAULT_RELEASES_URL, EDGE_URL]
    assert checked.edge.commit == NEWER and checked.release.version == "9.9.9"
    assert selfupdate.newer_edge(settings).commit == NEWER
    # and asks again sooner than a release does
    selfupdate.check(settings, now=2000.0 + selfupdate.EDGE_CHECK_EVERY - 1)
    assert len(latest) == 3
    selfupdate.check(settings, now=2000.0 + selfupdate.EDGE_CHECK_EVERY + 1)
    assert len(latest) == 5

    off = replace(settings, update_check=False)
    assert selfupdate.check(off, force=True) is selfupdate.state and len(latest) == 5
    assert selfupdate.newer_edge(off) is None
    other = replace(settings, releases_url="https://example.com/latest.json")
    selfupdate.check(other, force=True, now=9000.0)
    assert latest[5:] == ["https://example.com/latest.json"]  # no telling where to ask


def test_newer_edge_is_another_commit_than_the_one_running(settings, latest, revision_file):
    revision_file.write_text(NEWER, encoding="ascii")
    selfupdate.check(settings, now=1000.0)
    assert selfupdate.state.edge.commit == NEWER and selfupdate.newer_edge(settings) is None
    revision_file.write_text(NEWER[:7], encoding="ascii")  # a short id is the same commit
    assert selfupdate.newer_edge(settings) is None
    revision_file.write_text(COMMIT, encoding="ascii")
    assert selfupdate.newer_edge(settings).commit == NEWER
    revision_file.unlink()  # a release never offers a development version
    assert selfupdate.newer_edge(settings) is None


def test_a_failed_edge_check_is_reported_and_keeps_what_was_known(settings, latest, revision_file):
    revision_file.write_text(COMMIT, encoding="ascii")
    selfupdate.check(settings, now=1000.0)
    latest.answer["edge"] = Answer({"message": "rate limited"}, status=403)
    checked = selfupdate.check(settings, force=True, now=2000.0)
    assert checked.error == "HTTP 403" and checked.edge.commit == NEWER
    latest.answer["edge"] = Answer({"message": "Not Found"})
    assert selfupdate.check(settings, force=True, now=3000.0).error == "unexpected answer"
    latest.answer["edge"] = Answer({"workflow_runs": []})  # nothing published yet
    checked = selfupdate.check(settings, force=True, now=4000.0)
    assert checked.error == "" and checked.edge is None


def test_admin_shows_the_latest_development_version(client, latest, revision_file):
    latest.answer["value"] = Answer(github(__version__))
    page = client.get("/admin").text
    assert "Latest development version" not in page  # a release says nothing about it
    assert "You have the latest version." in page and len(latest) == 1

    revision_file.write_text(COMMIT, encoding="ascii")
    client.post("/admin/version/check")
    page = client.get("/admin").text
    when = admin_page.local_time("2026-11-14T10:00:00Z")
    assert (
        f"<dt>Latest development version</dt>\n    <dd>commit 9bc60dc · {when}<br>Admin: say more</dd>"
        in page
    )
    assert "A longer explanation." not in page  # the first line of the message only
    assert "<strong>A newer development version is available: commit 9bc60dc.</strong>" in page
    assert "docker compose pull\ndocker compose up -d" in page
    assert "You have the latest" not in page

    latest.answer["edge"] = Answer(runs(COMMIT, message="<b>bold</b> & co"))
    client.post("/admin/version/check")
    page = client.get("/admin").text
    assert "commit 199f1a1 ·" in page and "&lt;b&gt;bold&lt;/b&gt; &amp; co" in page
    assert "You have the latest development version." in page
    assert "is available" not in page and "docker compose pull\n" not in page
    assert "You have the latest version." not in page

    latest.answer["edge"] = Answer({"workflow_runs": []})
    client.post("/admin/version/check")
    page = client.get("/admin").text
    assert "<dt>Latest development version</dt>\n    <dd>—</dd>" in page
    assert "development version is available" not in page


def test_a_development_version_is_told_of_a_release_but_updated_as_one(
    client, latest, revision_file, monkeypatch
):
    monkeypatch.setattr(selfupdate, "install_kind", lambda: "docker")
    revision_file.write_text(COMMIT, encoding="ascii")
    page = client.get("/admin").text
    assert "Version 9.9.9 is available." in page and "What is new" in page
    # one set of commands, for the development version
    assert page.count("docker compose pull\ndocker compose up -d") == 1
    assert page.index("Version 9.9.9 is available.") < page.index("A newer development version")

    client.get("/lang/it")
    page = client.get("/admin").text
    assert "<dt>Ultima versione di sviluppo</dt>" in page
    assert "È disponibile una versione di sviluppo più recente: commit 9bc60dc." in page
    latest.answer["edge"] = Answer(runs(COMMIT))
    client.post("/admin/version/check")
    assert "ultima versione di sviluppo." in client.get("/admin").text


def test_the_header_shows_the_new_name(admin):
    page = admin.get("/").text
    assert '<a class="brand" href="/" title="Another Magic Tracking Gizmo">AMTG</a>' in page
    assert "<title>AMTG</title>" in page
