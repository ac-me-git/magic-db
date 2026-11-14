# Working rules for magic-db

You are implementing the project described in `SPEC.md`, following `PLAN.md`.

## Every session

1. Read `SPEC.md` and the status table in `PLAN.md`. Work on the first milestone that is
   not `done` — only that one.
2. Write the tests listed under "Done when" together with the code, not afterwards.
3. Before finishing: `uv run ruff check .`, `uv run ruff format .`, `uv run pytest`.
   All three must pass. If something fails and you cannot fix it, say so plainly; never
   weaken, skip or delete a test to get a green run.
4. Update the status table in `PLAN.md` (status, date, anything the reviewer should
   know), commit, push to `origin` (`main`), and stop. Do not start the next milestone.

## Hard rules

- **Do not change** the database schemas (§4.3, §5) or the search grammar and filter
  table (§6) on your own. If the spec looks wrong or incomplete, stop and ask.
- **No new dependencies** beyond those in SPEC §2 without asking.
- **No ORM, no Node, no CDN links.** SQL is written by hand; front-end assets are
  vendored files in `static/`.
- **Every SQL value is a bound parameter.** Never build SQL by concatenating or
  formatting user input. Column and table names come only from constants in the code.
- **Tests never use the network.** Mock `httpx`; use `tests/fixtures/`.
- **Never touch a real `data/` directory in tests** — use `tmp_path`.
- The code must run on Windows and Linux: use `pathlib`, never hard-code `/` paths,
  never assume an open file can be replaced or deleted.
- Push every commit to `origin` (`main`) without asking: the owner asked for this once
  and for all (2026-11-14). A push publishes the `edge` Docker image, never a release.
  Do not tag or publish releases unless the owner asks in that session.
- Do not commit card images, the Scryfall bulk file, any `data/` content, or a real
  collection export. The TopDecked sample in `tests/fixtures/` must be trimmed and must
  not contain personal notes.

## Style

- Python 3.12, type hints on public functions, small modules, plain functions over
  classes where a class adds nothing.
- Routes are thin: parse input, call a service function, render a template.
- Code, comments, commit messages and docs in English. UI strings only through
  `i18n/*.json`.
- Commit messages: imperative, one line, prefixed with the milestone (`M2: add colour
  operators`).

## When in doubt

Prefer the simplest thing that satisfies the spec and its tests. Leave a short
`TODO(owner):` note in `PLAN.md` rather than inventing a feature.
