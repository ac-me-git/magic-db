"""Keep a history of the project's numbers on GitHub: release downloads and traffic.

    python scripts/github_stats.py [--out FOLDER] [--repo OWNER/NAME]

GitHub keeps the traffic (visits, clones, referring sites) for 14 days only, and the
download counters of the release files without any date. Run every day, or at least
once every 14 days, this script keeps what GitHub reports in three CSV files:

- `traffic.csv`: one row per day (UTC): views, unique visitors, clones, unique
  cloners. A day GitHub still reports is updated with its latest figures, so the days
  between two runs are filled in too.
- `downloads.csv`: one row per release file and run date: its download counter.
- `referrers.csv`: one row per referring site and run date: its 14-day totals.

Each run also adds a line to `runs.log`. It asks through the GitHub CLI (`gh`),
signed in as someone who may read the repository's traffic (its owner). Standard
library only. The folder holds the owner's data: it does not belong in the repository.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

REPO = "ac-me-git/magic-db"
OUT = Path.home() / "amtg-stats"

TRAFFIC = ["date", "views", "unique_views", "clones", "unique_clones"]
DOWNLOADS = ["date", "release", "file", "downloads"]
REFERRERS = ["date", "referrer", "views", "unique_views"]

Api = Callable[[str], object]


def gh_api(path: str) -> object:
    """One GET of the GitHub API through the `gh` command line, as JSON."""
    gh = shutil.which("gh")
    if gh is None:
        raise RuntimeError("the GitHub CLI (gh) is not installed or not on PATH")
    done = subprocess.run(
        [gh, "api", path],
        capture_output=True,
        text=True,
        encoding="utf-8",
        # run by the scheduled task without a console: no window flashes up for gh
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if done.returncode:
        raise RuntimeError(f"gh api {path} failed: {done.stderr.strip()}")
    return json.loads(done.stdout)


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def write_rows(path: Path, fields: list[str], rows: list[dict]) -> None:
    """Write next to the file, then put it in place: a failed run leaves the old one."""
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fields)
        writer.writeheader()
        writer.writerows(rows)
    try:
        temp.replace(path)
    except PermissionError:  # on Windows, a spreadsheet that has it open locks it
        temp.unlink(missing_ok=True)
        raise RuntimeError(f"{path.name} is open in another program: close it") from None


def merge_traffic(old: list[dict], views: dict, clones: dict) -> list[dict]:
    """Old days are kept; the days GitHub still reports take its latest figures."""
    fresh: dict[str, dict] = {}
    for kind, data in (("views", views), ("clones", clones)):
        for item in data[kind]:
            day = item["timestamp"][:10]
            row = fresh.setdefault(day, dict.fromkeys(TRAFFIC, 0) | {"date": day})
            row[kind] = item["count"]
            row[f"unique_{kind}"] = item["uniques"]
    days = {row["date"]: row for row in old} | fresh
    return [days[day] for day in sorted(days)]


def merge_snapshot(old: list[dict], today: str, new: list[dict]) -> list[dict]:
    """A second run on the same day replaces that day's rows."""
    return [row for row in old if row["date"] != today] + new


def collect(out: Path, repo: str, api: Api, today: str) -> str:
    """Ask GitHub, then update the three files. Returns a one-line summary."""
    base = f"repos/{repo}"
    # everything is asked before anything is written
    views = api(f"{base}/traffic/views")
    clones = api(f"{base}/traffic/clones")
    referrers = api(f"{base}/traffic/popular/referrers")
    releases = api(f"{base}/releases?per_page=100")

    downloads = [
        {
            "date": today,
            "release": release["tag_name"],
            "file": asset["name"],
            "downloads": asset["download_count"],
        }
        for release in reversed(releases)  # oldest first
        for asset in release["assets"]
    ]
    sites = [
        {
            "date": today,
            "referrer": site["referrer"],
            "views": site["count"],
            "unique_views": site["uniques"],
        }
        for site in referrers
    ]

    out.mkdir(parents=True, exist_ok=True)
    traffic = merge_traffic(read_rows(out / "traffic.csv"), views, clones)
    write_rows(out / "traffic.csv", TRAFFIC, traffic)
    old = read_rows(out / "downloads.csv")
    write_rows(out / "downloads.csv", DOWNLOADS, merge_snapshot(old, today, downloads))
    old = read_rows(out / "referrers.csv")
    write_rows(out / "referrers.csv", REFERRERS, merge_snapshot(old, today, sites))

    total = sum(row["downloads"] for row in downloads)
    return (
        f"{len(traffic)} days of traffic, {total} downloads of {len(downloads)} files, "
        f"{len(sites)} referring sites"
    )


def main(argv: list[str] | None = None, api: Api = gh_api) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=OUT, help=f"folder (default {OUT})")
    parser.add_argument("--repo", default=REPO, help=f"OWNER/NAME (default {REPO})")
    args = parser.parse_args(argv)

    now = datetime.now(UTC)
    try:
        line, code = "ok: " + collect(args.out, args.repo, api, now.date().isoformat()), 0
    except (RuntimeError, OSError, KeyError, ValueError) as error:
        line, code = f"failed: {error}", 1
    print(line)
    try:
        args.out.mkdir(parents=True, exist_ok=True)
        with (args.out / "runs.log").open("a", encoding="utf-8") as log:
            log.write(f"{now:%Y-%m-%d %H:%M} UTC {line}\n")
    except OSError:
        pass  # the line is printed anyway
    return code


if __name__ == "__main__":
    sys.exit(main())
