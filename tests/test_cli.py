"""Starting the server: command line, and the Windows executable's behaviour (SPEC §10)."""

import sys
import zipfile
from pathlib import Path

import pytest

from magicdb import cli
from magicdb.config import load_settings

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def served(monkeypatch, tmp_path):
    """Run main() without a real server or browser; record what it would have done."""
    record: dict[str, object] = {"browser": []}

    def fake_run(app, host, port):
        record.update(app=app, host=host, port=port)

    class FakeTimer:
        def __init__(self, delay, function, args):
            record["browser"].append((delay, function, args))
            self.daemon = False

        def start(self):
            record["started"] = True

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)
    monkeypatch.setattr(cli.threading, "Timer", FakeTimer)
    monkeypatch.setenv("MAGICDB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MAGICDB_PORT", "8123")
    return record


def as_executable(monkeypatch, folder):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(folder / "magic-db.exe"))


def test_serve_is_the_default_and_does_not_open_a_browser(served, capsys):
    assert cli.main([]) == 0
    assert (served["host"], served["port"]) == ("127.0.0.1", 8123)
    assert served["app"].state.settings.port == 8123
    assert served["browser"] == []
    out = capsys.readouterr().out
    assert "http://127.0.0.1:8123/" in out and "data in" in out


def test_browser_can_be_asked_for(served):
    assert cli.main(["--browser"]) == 0
    ((delay, function, args),) = served["browser"]
    assert function is cli.webbrowser.open and args == ["http://127.0.0.1:8123/"]
    assert delay > 0 and served["started"]
    served["browser"].clear()
    assert cli.main(["serve", "--browser"]) == 0 and len(served["browser"]) == 1


def test_the_executable_opens_the_browser_unless_told_not_to(served, monkeypatch, tmp_path):
    as_executable(monkeypatch, tmp_path)
    assert cli.main([]) == 0 and len(served["browser"]) == 1
    served["browser"].clear()
    assert cli.main(["--no-browser"]) == 0 and served["browser"] == []


def test_the_executable_keeps_its_data_beside_itself(monkeypatch, tmp_path):
    folder = tmp_path / "Magic DB"
    folder.mkdir()
    assert "MAGICDB_DATA_DIR" not in cli.environment({})  # a normal run is left alone
    as_executable(monkeypatch, folder)
    assert cli.is_executable()
    settings = load_settings(cli.environment({}))
    assert settings.data_dir == folder.resolve() / "data"
    assert settings.backup_dir == folder.resolve() / "data" / "backups"
    # an explicit choice still wins; a blank one does not count as a choice
    assert (
        cli.environment({"MAGICDB_DATA_DIR": "D:/elsewhere"})["MAGICDB_DATA_DIR"] == "D:/elsewhere"
    )
    assert cli.environment({"MAGICDB_DATA_DIR": "  "})["MAGICDB_DATA_DIR"].endswith("data")


def test_local_url():
    assert cli.local_url(load_settings({})) == "http://127.0.0.1:8080/"
    everywhere = load_settings({"MAGICDB_HOST": "0.0.0.0", "MAGICDB_PORT": "9000"})
    assert cli.local_url(everywhere) == "http://127.0.0.1:9000/"
    assert (
        cli.local_url(load_settings({"MAGICDB_HOST": "192.168.1.5"})) == "http://192.168.1.5:8080/"
    )


def test_bad_settings_are_explained(monkeypatch, capsys):
    monkeypatch.setenv("MAGICDB_PORT", "abc")
    with pytest.raises(SystemExit) as excinfo:
        cli.main([])
    assert excinfo.value.code == 2 and "MAGICDB_PORT" in capsys.readouterr().err


def test_executable_that_cannot_start_says_why_and_waits(monkeypatch, capsys):
    waited = []
    monkeypatch.setattr("builtins.input", lambda prompt="": waited.append(prompt))

    def port_taken(argv=None):
        raise OSError(10048, "address already in use")

    monkeypatch.setattr(cli, "main", port_taken)
    assert cli.run_executable() == 1
    assert "could not start" in capsys.readouterr().err and len(waited) == 1

    def stopped(argv=None):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "main", stopped)
    assert cli.run_executable() == 0 and len(waited) == 1  # Ctrl+C is not an error

    monkeypatch.setattr(cli, "main", lambda argv=None: 0)
    assert cli.run_executable() == 0

    def version_printed(argv=None):
        raise SystemExit(0)

    monkeypatch.setattr(cli, "main", version_printed)
    assert cli.run_executable() == 0 and len(waited) == 1


# --- what goes into the packages ------------------------------------------------------


def test_every_data_file_of_the_package_is_of_a_kind_the_builds_include():
    """The wheel and the executable carry the whole package folder; this guards the
    assumption that nothing the app needs lives outside it."""
    package = ROOT / "src" / "magicdb"
    kinds = {p.suffix for p in package.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    assert kinds == {".py", ".html", ".css", ".js", ".json", ".sql"}
    for needed in [
        "templates/base.html",
        "static/app.css",
        "static/htmx.min.js",
        "i18n/en.json",
        "i18n/it.json",
        "migrations/0001_init.sql",
        "catalog/schema.sql",
        "catalog/indexes.sql",
    ]:
        assert (package / needed).is_file(), needed


def test_packaging_files_agree_with_the_code():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER magicdb" in dockerfile and "HEALTHCHECK" in dockerfile
    assert "MAGICDB_DATA_DIR=/data" in dockerfile and "MAGICDB_HOST=0.0.0.0" in dockerfile
    assert 'CMD ["magicdb"]' in dockerfile
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'magicdb = "magicdb.cli:main"' in pyproject
    build = (ROOT / "scripts" / "build_exe.py").read_text(encoding="utf-8")
    assert '"--collect-data",\n    "magicdb"' in build and "launcher.py" in build
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "magic-db-data:/data" in compose and "8080:8080" in compose


def test_changes_are_published_as_edge_and_only_releases_as_latest():
    workflows = ROOT / ".github" / "workflows"
    job = (workflows / "ci.yml").read_text(encoding="utf-8").split("\n  edge:\n")[1]
    steps = "\n".join(line for line in job.splitlines() if not line.lstrip().startswith("#"))
    assert "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in steps
    assert "needs: [test, docker]" in steps  # nothing that fails the tests is published
    assert "tags: ghcr.io/${{ github.repository }}:edge" in steps
    assert steps.count("tags:") == 1  # and that is the only name it is published under
    assert ":latest" not in steps and "value=latest" not in steps and "semver" not in steps
    release = (workflows / "release.yml").read_text(encoding="utf-8")
    assert 'tags: ["v*"]' in release and "type=raw,value=latest" in release
    assert "branches" not in release
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "\n    image: ghcr.io/ac-me-git/magic-db:latest\n" in compose


def test_wheel_contents_helper_understands_a_wheel(tmp_path):
    """wheel_data_files() is what the release checklist uses to inspect a built wheel."""
    wheel = tmp_path / "x.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("magicdb/app.py", "")
        archive.writestr("magicdb/templates/base.html", "")
        archive.writestr("magicdb-0.1.0.dist-info/METADATA", "")
    assert wheel_data_files(wheel) == ["magicdb/templates/base.html"]


def wheel_data_files(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as archive:
        return sorted(
            name
            for name in archive.namelist()
            if name.startswith("magicdb/") and not name.endswith(".py")
        )
