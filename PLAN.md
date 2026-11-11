# magic-db — Execution plan

Ten milestones. **One milestone per work session.** Each ends with the listed tests
green, a commit, and the status table below updated. Stop there and wait for review —
do not start the next milestone in the same session.

Read `SPEC.md` first; section numbers below (§) refer to it.

## Status

| # | Milestone | Status | Notes |
|---|---|---|---|
| 0 | Skeleton, CI, fixture | done 2026-11-11 | 23 tests green. Fixture: 263 cards, 48 sets. Built locally with venv + pip on Python 3.13 (`uv` not installed on the dev PC), so there is no `uv.lock` yet and the CI workflow has never run. htmx 2.0.4 vendored. Minimal i18n (`t()`, en/it) already in place. Scryfall rate-limits hard: keep `make_fixture.py` at ≤1 request/s. Starlette warns that `httpx` in `TestClient` is deprecated in favour of `httpx2` — TODO(owner): decide whether to switch. |
| 1 | Catalog ingest | done 2026-11-11 | 58 tests green. Real ingest on the dev PC: 120,054 printings (39,288 cards, 1,067 sets) in 153 s, peak memory 82 MB, catalog file 176 MB. Scryfall now serves `jsonl_download_uri` (gzipped JSON Lines) instead of `download_uri`: the reader handles both (SPEC §4.1 updated). Migrations moved to `src/magicdb/migrations/` so they ship with the package. `magicdb update` has `--from-file`, `--sets-file`, `--force`, `--min-rows`. **For M2:** on the real catalog a full-scan `LIKE` takes ~140 ms, but `… GROUP BY oracle_id` took ~640 ms — check the query plan (the planner probably walks `ix_p_oracle`) before settling the `unique:cards` SQL. `pref` picks The List (`plst`) reprints as the default printing of many cards, e.g. Lightning Bolt: the owner has seen this and decided to keep the formula as it is (2026-11-11). |
| 2 | Search engine | done 2026-11-11 | 311 tests green: 23 parser error cases, 125 query cases checked against predicates computed from the fixture JSON (each in both `unique` modes), 37 error messages, 35 hostile inputs. `search.run(conn, query, page, currency=, per_page=)` returns a `Result` (rows, total, page, pages, unique, order, direction, suggestions). `FILTERS` in `search/compiler.py` carries `summary` and `example` for the `/syntax` page. Decisions recorded in SPEC §6.3: query shape chosen by measurement, directives are global. Limits: 2,000 characters, 100 terms, nesting 20. Real catalog: 115–280 ms per search. Reversible cards now take `cmc` from their first face (SPEC §4.3). Known gap: the fixture has no promo printing, so `is:promo` is only tested for "no error". For M3: owned-quantity badges are not part of `Result`; query them for the page's ids. |
| 3 | Browsing UI | todo | |
| 4 | Collection | todo | |
| 5 | Import / export | todo | needs `tests/fixtures/topdecked-sample.csv` from the owner |
| 6 | Statistics | todo | |
| 7 | Scheduler, backups, admin | todo | |
| 8 | Packaging | todo | |
| 9 | Documentation and release | todo | |

---

## M0 — Skeleton, CI, fixture

Build
- `git init`, `pyproject.toml` (package `magicdb`, entry point `magicdb`), layout of §2,
  `.gitignore` (`data/`, `.venv/`, caches), `LICENSE` (MIT), stub `README.md`.
- `config.py` (§3). App factory with `/healthz` and a placeholder home page using the
  base template, stylesheet and vendored `htmx.min.js`.
- `scripts/make_fixture.py` and the generated `tests/fixtures/cards.json` (§12), plus
  `tests/fixtures/sets.json` for the sets involved.
- GitHub Actions workflow: ruff + pytest on `ubuntu-latest` and `windows-latest`.

Done when
- `uv run magicdb` serves the home page on `http://127.0.0.1:8080`.
- `uv run pytest` passes (`/healthz` test, config defaults test, a test asserting the
  fixture contains every case listed in §12).

## M1 — Catalog ingest

Build
- `catalog/schema.sql`, `catalog/ingest.py`, `catalog/publish.py` implementing §4
  exactly, including mapping rules, `pref`, `facets`, `names`, sanity check and the
  versioned-file publish.
- `db.py`: live-catalog lookup, per-request connection with `ATTACH … AS cat`,
  collection migration runner with `migrations/0001_init.sql` (§5 schema).
- CLI: `magicdb update` (real download) and `magicdb update --from-file <json>`.

Done when
- Tests on the fixture: row count; one assertion per mapping rule (DFC colours and
  images, split-card text, `*` power → `power_num IS NULL`, colour bitmasks, keywords
  and `legal_in` format, `is_extra`, `pref` ordering among the printings of one card,
  `facets` contain `Dragon` as subtype and `Flying` as keyword).
- A failed sanity check leaves the previous catalog live (test).
- Publishing while a connection to the old catalog is open works on Windows (test).
- Manually: `magicdb update` ingests the real bulk file; note time and peak memory in
  the status table.

## M2 — Search engine

Build
- `search/tokenizer.py`, `search/parser.py`, `search/compiler.py` per §6.1–6.3, a
  single `FILTERS` table that drives both the compiler and the `/syntax` page.
- `search.run(conn, query, page) -> (rows, total, directives)`; fuzzy fallback.

Done when
- Parser tests: precedence of `or`, parentheses, negation, quoted values, every
  operator, error messages for unknown key / bad number / unbalanced parenthesis.
- At least 60 query tests against the fixture catalog, covering every row of the §6.2
  table, each colour operator, `id:` default semantics, `unique:cards` vs
  `unique:prints`, extras hidden by default, ordering and paging.
- Collection filters (`have`, `haveprint`, `qty`, `loc`, `cond`, `finish`, `lang`)
  tested with entries inserted directly by the test.
- A test that feeds hostile strings (`'; DROP TABLE`, unbalanced quotes, 10 kB input)
  and gets either results or a `QueryError`, never an exception.

## M3 — Browsing UI

Build
- `/`, `/search` (grid and list views, paging via HTMX), `/card/{id}`, `/syntax`,
  filter panel (§6.4), `/img/...` cache proxy, `/api/autocomplete`, i18n (`en`, `it`).

Done when
- Route tests: each page returns 200 and contains expected text; a bad query shows the
  error message with status 200; the filter panel form produces the expected query
  string (test the builder function for 10 combinations).
- Image proxy tests with a mocked HTTP client: miss → download → hit; failure →
  placeholder.
- Manually on a 390 px wide viewport: search, open a card, flip a DFC.

## M4 — Collection

Build
- `collection/service.py`: add, edit, move, remove, location CRUD, with the merge rules
  and `history` rows of §5. All SQL lives here; routes stay thin.
- `/add` quick add, `/collection`, `/locations`, the add form and "my copies" block on
  the card page, owned-quantity badges in search results.

Done when
- Service tests: add twice merges; edit into a collision merges; partial and full move;
  move to the same location is a no-op; delete of a non-empty location is refused;
  every write produces one history row; `quantity` can never reach 0 or below.
- Route tests for quick add (sticky fields survive the next request) and inline edit.
- `Origin` check test on a POST route.

## M5 — Import / export

Prerequisite: the owner has placed a real TopDecked export at
`tests/fixtures/topdecked-sample.csv` (trim it to ~30 rows). If it is missing, build
everything else and leave the `topdecked` profile as the only open item.

Build
- `exporters.py` (CSV, JSON), importer framework with profiles `native`, `topdecked`,
  `moxfield`, `manabox`, resolution order and two-step preview/confirm flow of §8.

Done when
- Round-trip test: export CSV → import into an empty collection → identical entries and
  locations. Same for JSON.
- Resolution tests: by id, by set + number, by name + set, ambiguous name, unknown card.
- Preview writes nothing (test); confirm creates a `pre-import-` backup first (test).
- Condition / finish / language value translations tested per profile.

## M6 — Statistics

Build
- `stats.py` with one function per block of §9, `/stats` page with SVG/CSS bars and the
  location selector.

Done when
- Tests with a hand-built collection on the fixture: totals; value with foil / etched /
  missing-price fallbacks; each breakdown; set completion of `arn` at 0 %, partial and
  100 %; the "missing cards" link returns exactly the missing printings.

## M7 — Scheduler, backups, admin

Build
- `jobs.py`: scheduler thread, due-job logic, backup + integrity check + CSV, rotation
  (§8). `/admin` page with status, job history, "update now", "back up now", download.
- First-run experience: no catalog → ingest starts, pages show progress.

Done when
- Rotation tested as a pure function over a year of simulated dates.
- Due-job logic tested with a fake clock: normal night, PC off for three days, job
  already done today, job currently running.
- Backup test: the produced file opens, passes `integrity_check`, has the same entries.
- Jobs never overlap (test).

## M8 — Packaging

Build
- `Dockerfile`, `compose.yaml`, release workflow (image to `ghcr.io`, PyInstaller
  `magic-db.exe` attached to the GitHub release), Windows launcher behaviour of §10.

Done when
- `docker compose up` on a clean machine reaches a searchable catalog with no manual
  step; the container runs as non-root and reports healthy.
- The CI-built exe starts on Windows, creates `data/` beside itself and opens the
  browser. Record both checks in the status table.

## M9 — Documentation and release

Build
- `README.md` (what it is, screenshots, quick start for Docker and Windows,
  configuration table, backups and restore, exposing it remotely behind a reverse
  proxy or access gateway, legal notices).
- `docs/`: `search-syntax.md`, `import-export.md`, `backup-restore.md`,
  `development.md`. `CONTRIBUTING.md`, `CHANGELOG.md`.
- Tag `v1.0.0`.

Done when
- Someone who has never seen the project installs it following only the README.
- Every environment variable in `config.py` appears in the README table (test).
