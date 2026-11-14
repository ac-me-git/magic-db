# Development

## Setting up

```sh
git clone https://github.com/ac-me-git/magic-db.git
cd magic-db
uv sync
uv run magicdb            # http://127.0.0.1:8080
```

Without uv: `python -m venv .venv`, activate it, `pip install -e . pytest ruff`.

Before every commit:

```sh
uv run ruff check .
uv run ruff format .
uv run pytest
```

Tests never use the network and never touch a real data folder: they build a small
catalog from `tests/fixtures/cards.json` in a temporary directory.

## How it is put together

Python 3.12, FastAPI with plain `def` endpoints, Jinja templates, HTMX for a few partial
updates, SQLite through the standard `sqlite3` module. No ORM, no Node, no build step,
no CDN at run time. [SPEC.md](../SPEC.md) is the source of truth for what the app does
and why; read it before changing behaviour.

```
src/magicdb/
  app.py            the application factory and its middlewares (password, same-origin, lock)
  cli.py            command line, and what the Windows executable does at start
  config.py         settings from environment variables
  db.py             connections and collection migrations
  catalog/          building the card catalog from Scryfall (ingest, publish)
  search/           tokenizer -> parser -> SQL compiler; panel.py turns the filter form into a query
  collection/       service.py (all collection SQL), routes, import/export
  decks/            deck lists: service, text parser, routes
  stats.py          the statistics page's numbers
  jobs.py           nightly catalog update and backup, rotation, scheduler
  lock.py           the safety lock
  images.py, symbols.py   download-once caches for card images and mana symbols
  templating.py     the Jinja environment, per-visitor language
  admin.py, web.py  pages
  i18n/             en.json, it.json
  migrations/       0001_init.sql, 0002_…  (collection database)
  templates/  static/
```

Two databases:

- `data/catalog/cards-<timestamp>.db` — the card catalog. Disposable, rebuilt from
  Scryfall, never migrated: a new one simply replaces the old. Attached read-only to
  every connection as `cat`.
- `data/collection.db` — the collection, locations, decks, settings, history. Precious.
  Changes to its schema are numbered SQL files in `src/magicdb/migrations/`, applied at
  startup; a backup is taken first.

## Rules worth knowing

- **Every SQL value is a bound parameter.** Column and table names come only from
  constants in the code, never from a request.
- **Routes are thin**: read the form, call a service function, render or redirect. SQL
  lives in the services.
- **Every change is a POST followed by a redirect**, so reloading a page never repeats it.
- **The safety lock denies by default**: any request that is not a plain read is refused
  while the collection is locked, unless `lock.allowed_while_locked` lists it. A new page
  needs no code for the lock, but should hide its forms with
  `{% if locked %}{% include "_locked_notice.html" %}{% else %}…{% endif %}`.
- **Interface text goes through `t()`** and exists in both `i18n/en.json` and
  `i18n/it.json`; a test fails if the two disagree.
- **The search syntax has one definition**: the `FILTERS` table in `search/compiler.py`.
  The in-app Syntax page and `docs/search-syntax.md` are generated from it — after
  changing it, run `python scripts/make_syntax_doc.py`.
- **Nothing the app needs at run time may live outside `src/magicdb/`**: that folder is
  what goes into the wheel, the Docker image and the Windows executable.
- The code must run on Windows and Linux: `pathlib`, no hard-coded `/`, and never assume
  an open file can be replaced or deleted.

## Test data

`tests/fixtures/cards.json` is about 260 real card objects from Scryfall, chosen to
cover the awkward cases (two-faced cards, split cards, `*` power, tokens, one complete
small set). To regenerate it: `python scripts/make_fixture.py` — it makes fewer than ten
requests, one per second; Scryfall rate-limits hard.

`tests/fixtures/topdecked-sample.csv` reproduces the structure of a real TopDecked
export with cards of the fixture (`scripts/make_topdecked_sample.py`). Real collection
exports must never be committed: `tests/fixtures/*.csv` is ignored except `*-sample.csv`.

## Building the packages

```sh
docker build -t magic-db .                 # the image
uv pip install pyinstaller
uv run python scripts/build_exe.py         # dist/magic-db.exe (on Windows)
```

## Releasing

1. Update `CHANGELOG.md` and the version in `pyproject.toml` and
   `src/magicdb/__init__.py`.
2. Make sure CI is green on `main`.
3. Tag and push: `git tag v1.2.3 && git push origin v1.2.3`.

The release workflow runs the tests, publishes the image to
`ghcr.io/<owner>/magic-db` and attaches `magic-db.exe` to the GitHub release.

## The development image

Every push to `main` that passes CI is published as `ghcr.io/<owner>/magic-db:edge` (the
`edge` job of `ci.yml`), for trying a change on a real server before releasing it. It
never touches `latest` or the version tags, and makes no GitHub release.

The image is built with its commit (`--build-arg REVISION=…`, which the Dockerfile writes
to `magicdb/REVISION`), so its Administration page shows "development version, commit …"
beside the version number. A release, the executable and a checkout have no such file and
show nothing. A development version also asks GitHub for the newest run of the CI workflow
on `main` that passed — the commit last published as `edge` — and the page says whether
that is the one running. The same commit is the image's
`org.opencontainers.image.revision` label:

```sh
docker image inspect ghcr.io/<owner>/magic-db:edge --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}'
```
