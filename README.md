# magic-db

Self-hosted web app to browse every Magic: The Gathering card and track your own
collection. Runs on a Windows PC or in a Linux Docker container.

**Status: early development.** See `SPEC.md` for what is being built and `PLAN.md` for
progress.

## Development

```
uv sync
uv run magicdb          # http://127.0.0.1:8080
uv run pytest
```

## Legal

magic-db is unofficial Fan Content permitted under the Fan Content Policy. Not
approved/endorsed by Wizards. Portions of the materials used are property of Wizards of
the Coast. ©Wizards of the Coast LLC.

Card data is provided by [Scryfall](https://scryfall.com). This repository contains no
card images and no bulk card data, apart from a small test fixture.

Code is released under the MIT licence.
