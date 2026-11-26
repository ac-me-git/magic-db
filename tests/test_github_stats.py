"""scripts/github_stats.py: the history of downloads and traffic, from a fake GitHub."""

import csv
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("github_stats", ROOT / "scripts" / "github_stats.py")
stats = importlib.util.module_from_spec(spec)
sys.modules["github_stats"] = stats
spec.loader.exec_module(stats)

BASE = "repos/owner/app"


def day(date: str, count: int, uniques: int) -> dict:
    return {"timestamp": f"{date}T00:00:00Z", "count": count, "uniques": uniques}


def github(views=None, clones=None, referrers=None, releases=None) -> dict:
    return {
        f"{BASE}/traffic/views": {
            "views": views if views is not None else [day("2026-11-24", 5, 3)],
        },
        f"{BASE}/traffic/clones": {
            "clones": clones
            if clones is not None
            else [day("2026-11-24", 40, 9), day("2026-11-25", 12, 4)],
        },
        f"{BASE}/traffic/popular/referrers": referrers
        if referrers is not None
        else [{"referrer": "amtg.fans", "count": 4, "uniques": 3}],
        f"{BASE}/releases?per_page=100": releases
        if releases is not None
        else [
            {"tag_name": "v2.0.0", "assets": [{"name": "app.exe", "download_count": 7}]},
            {"tag_name": "v1.0.0", "assets": [{"name": "app.exe", "download_count": 3}]},
        ],
    }


def rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def run(out: Path, answers: dict, today: str = "2026-11-25") -> str:
    return stats.collect(out, "owner/app", answers.__getitem__, today)


def test_the_first_run_writes_the_three_files(tmp_path):
    summary = run(tmp_path, github())
    assert summary == "2 days of traffic, 10 downloads of 2 files, 1 referring sites"
    assert rows(tmp_path / "traffic.csv") == [
        {"date": "2026-11-24", "views": "5", "unique_views": "3"}
        | {"clones": "40", "unique_clones": "9"},
        # a day with clones and no views
        {"date": "2026-11-25", "views": "0", "unique_views": "0"}
        | {"clones": "12", "unique_clones": "4"},
    ]
    assert rows(tmp_path / "downloads.csv") == [
        {"date": "2026-11-25", "release": "v1.0.0", "file": "app.exe", "downloads": "3"},
        {"date": "2026-11-25", "release": "v2.0.0", "file": "app.exe", "downloads": "7"},
    ]
    assert rows(tmp_path / "referrers.csv") == [
        {"date": "2026-11-25", "referrer": "amtg.fans", "views": "4", "unique_views": "3"}
    ]


def test_a_later_run_keeps_the_old_days_and_updates_the_ones_github_still_reports(tmp_path):
    run(tmp_path, github(views=[day("2026-11-01", 2, 1), day("2026-11-24", 1, 1)], clones=[]))
    run(tmp_path, github(views=[day("2026-11-24", 5, 3), day("2026-11-30", 8, 6)], clones=[]))
    traffic = rows(tmp_path / "traffic.csv")
    assert [(row["date"], row["views"]) for row in traffic] == [
        ("2026-11-01", "2"),  # gone from GitHub's 14 days, kept here
        ("2026-11-24", "5"),  # the latest figure wins
        ("2026-11-30", "8"),
    ]


def test_a_second_run_on_the_same_day_replaces_that_days_snapshot(tmp_path):
    one = [{"tag_name": "v1", "assets": [{"name": "app.exe", "download_count": 3}]}]
    two = [{"tag_name": "v1", "assets": [{"name": "app.exe", "download_count": 5}]}]
    run(tmp_path, github(releases=one), today="2026-11-25")
    run(tmp_path, github(releases=one), today="2026-11-26")
    run(tmp_path, github(releases=two), today="2026-11-26")
    assert [(r["date"], r["downloads"]) for r in rows(tmp_path / "downloads.csv")] == [
        ("2026-11-25", "3"),
        ("2026-11-26", "5"),
    ]
    assert len(rows(tmp_path / "referrers.csv")) == 2  # one site, two dates


def test_a_failed_request_writes_nothing_and_says_so_in_the_log(tmp_path, capsys):
    run(tmp_path, github())
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}

    def broken(path: str) -> object:
        if path.endswith("/releases?per_page=100"):
            raise RuntimeError("gh api releases failed: HTTP 502")
        return github()[path]

    code = stats.main(["--out", str(tmp_path), "--repo", "owner/app"], api=broken)
    assert code == 1
    assert "failed: gh api releases failed: HTTP 502" in capsys.readouterr().out
    after = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.name != "runs.log"}
    assert after == before
    log = (tmp_path / "runs.log").read_text(encoding="utf-8").splitlines()
    assert len(log) == 1 and log[0].endswith("UTC failed: gh api releases failed: HTTP 502")


def test_a_good_run_is_logged(tmp_path):
    answers = github()
    assert stats.main(["--out", str(tmp_path), "--repo", "owner/app"], api=answers.get) == 0
    log = (tmp_path / "runs.log").read_text(encoding="utf-8")
    assert "UTC ok: 2 days of traffic, 10 downloads of 2 files" in log


def test_a_file_open_elsewhere_is_named_and_left_alone(tmp_path, monkeypatch):
    run(tmp_path, github())
    before = (tmp_path / "traffic.csv").read_bytes()

    def locked(self, target):
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(Path, "replace", locked)
    monkeypatch.setattr(stats.time, "sleep", lambda seconds: None)
    with pytest.raises(RuntimeError, match="traffic.csv is open in another program"):
        run(tmp_path, github(views=[day("2026-11-25", 9, 9)]))
    assert (tmp_path / "traffic.csv").read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


def test_a_file_held_for_a_moment_by_a_sync_client_is_written_after_all(tmp_path, monkeypatch):
    run(tmp_path, github())
    replace, refusals, waits = Path.replace, [1, 2], []

    def busy_twice(self, target):
        if refusals:
            refusals.pop()
            raise PermissionError(13, "The process cannot access the file")
        return replace(self, target)

    monkeypatch.setattr(Path, "replace", busy_twice)
    monkeypatch.setattr(stats.time, "sleep", waits.append)
    run(tmp_path, github(views=[day("2026-11-24", 9, 9)]), today="2026-11-26")
    assert rows(tmp_path / "traffic.csv")[0]["views"] == "9"
    assert waits == [1, 1]


def test_gh_is_asked_for_one_path_and_its_json_read(monkeypatch):
    calls = []

    def fake_run(command, **options):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps({"ok": True}), "")

    monkeypatch.setattr(stats.shutil, "which", lambda name: "/bin/gh")
    monkeypatch.setattr(stats.subprocess, "run", fake_run)
    assert stats.gh_api("repos/owner/app/traffic/views") == {"ok": True}
    assert calls == [["/bin/gh", "api", "repos/owner/app/traffic/views"]]


def test_gh_missing_or_failing_is_an_error(monkeypatch):
    monkeypatch.setattr(stats.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="not installed"):
        stats.gh_api("x")
    monkeypatch.setattr(stats.shutil, "which", lambda name: "/bin/gh")
    monkeypatch.setattr(
        stats.subprocess,
        "run",
        lambda command, **options: subprocess.CompletedProcess(command, 1, "", "HTTP 403"),
    )
    with pytest.raises(RuntimeError, match="HTTP 403"):
        stats.gh_api("x")
