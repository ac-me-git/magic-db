# Contributing

Thank you for wanting to help. magic-db is a small project with a narrow aim: a card
browser and collection tracker that one person can run at home and trust with their data.

## Reporting a problem

Open an issue and say what you did, what you expected and what happened. If it is about a
search, include the exact query. If it is about importing a file, include its header line
and two or three rows — **not your whole collection**.

## Proposing a change

For anything beyond a small fix, open an issue first. [SPEC.md](SPEC.md) describes what
the app does and why; a change in behaviour should change the spec too, in the same pull
request.

Some things are deliberately out of scope: user accounts, price history, a hosted
service. Others are simply not done yet and welcome.

## Making a change

Set up as described in [docs/development.md](docs/development.md), then:

- keep the change small and about one thing;
- write the tests with the code — the project has a test for nearly everything it does,
  and that is what makes it safe to change;
- run `ruff check .`, `ruff format .` and `pytest` before pushing: CI runs the same on
  Linux and Windows;
- do not add a runtime dependency without discussing it first. No ORM, no Node, no CDN;
- interface text goes in both `i18n/en.json` and `i18n/it.json`.

## What must never be committed

- Card images or Scryfall bulk data.
- Anything from a `data/` folder.
- A real collection export. Test files are small, synthetic, and named `*-sample.csv`.

## Translations

A new interface language is a new file in `src/magicdb/i18n/` with the same keys as
`en.json`, plus its code in `LANGS` in `config.py`. Card names and rules text stay in
English.

## Licence

By contributing you agree that your work is released under the [MIT licence](LICENSE).
