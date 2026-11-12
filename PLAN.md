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
| 2 | Search engine | done 2026-11-11 | 311 tests green: 23 parser error cases, 122 query cases checked against predicates computed from the fixture JSON (each in both `unique` modes), 36 error messages, 35 hostile inputs. `search.run(conn, query, page, currency=, per_page=)` returns a `Result` (rows, total, page, pages, unique, order, direction, suggestions). `FILTERS` in `search/compiler.py` carries `summary` and `example` for the `/syntax` page. Decisions recorded in SPEC §6.3: query shape chosen by measurement, directives are global. Limits: 2,000 characters, 100 terms, nesting 20. Real catalog: 115–280 ms per search. Reversible cards now take `cmc` from their first face (SPEC §4.3). Known gap: the fixture has no promo printing, so `is:promo` is only tested for "no error". For M3: owned-quantity badges are not part of `Result`; query them for the page's ids. |
| 3 | Browsing UI | done 2026-11-11 | 404 tests green. New modules: `web.py` (pages), `images.py` (cache proxy), `search/panel.py` (panel → query), `templating.py`. Checked by hand on the real catalog at 390 px and desktop width: home with panel, grid and list results, HTMX paging without reload, card page, turning over a two-faced card, syntax page; no horizontal scroll, no console errors. Details worth knowing: the panel form goes to `GET /filter`, which redirects to `/search?q=…`; the panel lives on the home page only and is not pre-filled from an existing query; cached image files carry the version from Scryfall's URL so a rescanned image replaces the old one; only `*.scryfall.io` hosts are fetched; numbers and prices follow the interface language. On phones the list and printings tables hide secondary columns (`.wide`) and repeat their content under the name (`.narrow`). TODO(owner): search error messages (`QueryError`) are English only, also in the Italian interface. **Added 2026-11-12 at the owner's request** (427 tests): mana and tap symbols are images in costs and rules text — `symbols.py`, SPEC §7. Use the `symbols` filter for any new place that shows a cost or rules text. |
| 4 | Collection | done 2026-11-12 | 561 tests green (63 service, 48 pages, 28 language). Before it, at the owner's request: **per-visitor interface language** with a selector in the footer (SPEC §7 i18n). New: `collection/service.py` (all collection SQL; `CollectionError` codes), `collection/forms.py` (choices, sticky cookie), `collection/routes.py`, templates `add`, `collection`, `entry`, `locations`, `_forms`. Checked by hand at 390 px on a copy of the real catalog: create a location, quick add with live suggestions and printings (83 for Lightning Bolt), confirmation with Undo, sticky fields, collection list, inline edit, badge in search results. Decisions recorded in SPEC §5 and §7: a whole-row move keeps the row (id and "added on" date); Enter without a printing shows the choices; Origin check also accepts `X-Forwarded-Host`. Not done: reordering locations is by up/down buttons, not drag and drop. For M5: the import must go through `service` so merge rules and history apply; `service.price_sql()` and `service.totals()` are there for M6. |
| 5 | Import / export | done 2026-11-12 | 672 tests green (112 for this milestone). New: `collection/exporters.py`, `collection/importers/` (`profiles.py` + framework), `collection/transfer.py` (pages), `service.import_entries`, `db.backup_collection`. **The owner's real TopDecked export (9,385 rows) imports completely**: 9,385 matched, 0 ambiguous, 0 failed, 9,363 entries, in under 2 s; exporting it and reading it back matches again. Its format quirks are documented in SPEC §8. The real file stays out of git (`tests/fixtures/*.csv` is ignored except `*-sample.csv`). Round trips tested: CSV, JSON, and through the pages (export → import on an empty collection → byte-identical export). Not verified: the `moxfield` and `manabox` profiles against real files; TopDecked's values for language and condition (the real file has those columns empty). Import pages were checked in the browser up to the preview; confirm is covered by tests only. Also: `tests/*` may exceed the line length (test data). For M7: `db.backup_collection(conn, settings, prefix)` is the backup primitive; rotation must keep `pre-import-` and `pre-migration-` files 30 days. |
| 6 | Statistics | done 2026-11-12 | 708 tests green (36 for this milestone). New: `stats.py`, route `/stats` (in `collection/routes.py`), template `stats.html`, link from the collection page. Expected numbers in the tests are counted from the fixture JSON, not from the catalog; the value rules are tested by setting exact prices in the test's own copy of the catalog. Checked in the browser on the owner's real collection (9,385 cards, 123 sets) at desktop and 390 px width: page loads in ~0.2 s after replacing a slow three-table join (SPEC §9). Also: location names can no longer contain a double quote. The header menu does not link to the statistics (no room on a phone); the link is on the collection page. |
| 7 | Scheduler, backups, admin | done 2026-11-12 | 747 tests green (39 for this milestone). New: `jobs.py` (due logic, backup, rotation, scheduler), `admin.py` + `admin.html`, progress in `ingest`, optional password, `MAGICDB_SCHEDULER`. Rotation is tested as a pure function over 400 simulated nights; due logic with a fake clock for the four cases of the plan plus first run and retries; the scheduler minute by minute over three days. Seen working on the real server: at first start the scheduler checked the catalog ("already up to date") and made the first automatic backup of the owner's collection (9,363 entries: 2.8 MB database + 1.3 MB CSV); after a restart it did not run them again. Not exercised for real: a catalog rebuild triggered by the scheduler (Scryfall had nothing new) and the progress display during a real download — both covered by tests only. Restore stays manual (SPEC §8) and is still to be documented in M9. |
| — | Safety lock (owner's request) | done 2026-11-12 | 779 tests green (32 for this feature). A switch that makes the collection read-only: `lock.py`, migration `0002_settings.sql`, a middleware in `app.py`, a bar in `base.html`, `locked.html` and `_locked_notice.html` (SPEC §7). Refusal is on the server and denies by default: any non-read request gets 423 while locked, except `POST /lock` and `POST /admin/…`. **When adding a page that changes the collection, nothing has to be done for the lock** — but wrap its form in `{% if locked %}{% include "_locked_notice.html" %}{% else %}…{% endif %}` so the page does not offer what would be refused. Seen on the real server: the first start upgraded the owner's database to schema 2 (a `pre-migration-` backup was made first), and with the lock on, add / remove / create-location attempts returned 423 and the collection stayed at 9,385 cards. A new collection starts unlocked. |
| — | Decks (owner's request) | done 2026-11-12 | 862 tests green (73 for this feature). Deck lists compared with the collection: `decks/service.py`, `decks/text.py` (deck-list parser), `decks/routes.py`, migration `0003_decks.sql`, templates `decks.html` and `deck.html`, a "In my decks" block on the card page, search filter `deck:`, decks in the JSON export/import (SPEC §14). Checked in the browser on a copy of the owner's collection with three trial decks, at desktop and 390 px: completion gauges, contested-cards list, deck page, locked state. At the owner's request the safety lock is now a sliding switch with a padlock in the header (top right) instead of a bar, and deck completion is a round gauge. "Syntax" moved from the header to the footer to make room for "Decks". Not done: no per-deck notes on single cards, basic lands count like any other card. **Added 2026-11-12 at the owner's request** (883 tests): a Statistics tab on every deck — `service.deck_stats`, `deck_stats.html`, charts shared with the collection statistics through `_charts.html`. Checked in the browser on a 58-card trial deck. |
| — | Header and search refinements (owner's request) | done 2026-11-13 | 931 tests green (48 for this). Suggestions under the search box; a split Search button to search all cards or only the collection; the header menu reduced to "My collection" (opens the statistics, now the collection's landing page) and "My decks"; column headers that sort, in search results and in the collection list; a new `type` order (SPEC §7 "Header"). New: `static/app.js` (the project's only own script; optional), `search.with_order` / `sort_links`, `GET /search/suggest`, `templating.asset`. Found while checking in the browser: after the change the browser kept the old style sheet, so static files now carry a fingerprint of their content. Checked on the owner's real collection at desktop and 390 px. The owner's request listed "Add cards" twice (items 3 and 4); item 4 was read as "Filters", since it said the page is reachable from the logo — the owner confirmed this reading (2026-11-13). |
| 8 | Packaging | done 2026-11-12, **Docker untested** | 789 tests green (10 for this milestone). New: `Dockerfile`, `compose.yaml`, `.dockerignore`, `scripts/build_exe.py`, `packaging/launcher.py`, `.github/workflows/release.yml`, a Docker job in `ci.yml`, executable behaviour in `cli.py` (SPEC §10). **Windows executable: built and tried for real** on the dev PC — 19.5 MB, 38 s to build; copied alone into an empty folder it started, created `data/` beside itself, downloaded and built the catalog by itself in 2 min 40 s while the pages showed the progress, then search, symbols, images, card page, statistics, quick add and export all answered. Not tried: the automatic opening of the browser (run with `--no-browser`; unit-tested only). **Docker: not installed on the dev PC, so the image has never been built or run.** What was checked instead: the wheel the image installs contains all 33 data files. The CI job that builds and starts the container, and the whole release workflow, have never run because the repository is not on GitHub yet — the first push is the real test. Also added: an `indexing` phase in the update progress (the page looked stuck during index creation). The executable is not signed: Windows SmartScreen will warn on first run — say so in the README (M9). `compose.yaml` assumes the image name `ghcr.io/ac-me-git/magic-db`. |
| 9 | Documentation and release | done 2026-11-12, **not tagged, not published** | 869 tests green (7 for this milestone). Written: `README.md`, `docs/search-syntax.md` (generated from the engine's `FILTERS` by `scripts/make_syntax_doc.py`; a test fails if it is out of date), `docs/decks.md`, `docs/import-export.md`, `docs/backup-restore.md`, `docs/development.md`, `CONTRIBUTING.md`, `CHANGELOG.md`. Version set to 1.0.0. Tests check that every setting in `config.py` is in the README table (and that no document mentions a setting that does not exist), that links between documents resolve, and that the version agrees everywhere. **Left for the owner:** (1) create the GitHub repository and push — nothing has been pushed; (2) see CI go green, which is the first real run of the Docker build and of the workflows; (3) only then tag `v1.0.0`, which publishes the image and the executable. Also not done: screenshots in the README (they would put card art in the repository, which SPEC §11 excludes — the owner should decide), and "someone who has never seen the project installs it from the README alone" has not been tried by anyone. `compose.yaml`, the README and the docs assume the repository is `github.com/ac-me-git/magic-db`. |
| + | Owner request: new name, version check and self-update | done 2026-11-12, **not pushed, not released** | 970 tests green (39 new, `tests/test_selfupdate.py`). Display name is now AMTG / Another Magic Tracking Gizmo (`app.name`, `app.full_name`); `magic-db` stays the technical name everywhere else. New `src/magicdb/selfupdate.py`, `templates/restarting.html`, a Software version section in `/admin`, settings `MAGICDB_UPDATE_CHECK` and `MAGICDB_RELEASES_URL` (SPEC §7). Version is 1.1.0. **Tried for real** on the dev PC with two locally built executables and a stand-in release server: 1.1.0 downloaded 9.9.9, swapped, restarted in 2 s, data kept, `.old` removed, and the waiting page returned to `/admin` by itself. **Not tried:** against the real GitHub API (needs a release newer than the running program), and a restart of an executable started by double click (console window behaviour). Tests never reach the network: `conftest.py` replaces `selfupdate._get/_stream`. The owner asked for an "automatic" update; built as one button, never unattended — TODO(owner): confirm. |

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
