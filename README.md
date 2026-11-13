# AMTG — Another Magic Tracking Gizmo

*(the repository, the program file and the Docker image are called `magic-db`)*

A self-hosted web app to browse every Magic: The Gathering card and keep track of your
own collection and decks. It runs on a Windows PC with nothing else to install, or in a
Docker container, and you use it from any browser — phone included.

- **Every card ever printed**, and the ones previewed for upcoming sets, updated every
  night from [Scryfall](https://scryfall.com).
- **Search that understands Magic**: colour, mana value, type, keyword abilities, rarity,
  set, format legality, price and more — typed (`t:dragon c:r mv<=4`) or picked from a
  filter panel that writes the query for you.
- **Your collection**: quantity, finish (foil, etched), condition, language and *where
  the card physically is* — binder, box, deck.
- **Decks**: build lists by hand or paste a deck list, and see how much of each deck you
  already own, and which cards several decks are competing for.
- **Statistics**: by colour, rarity, type, year; value; set completion.
- **Your data stays yours**: one SQLite file, exported as CSV or JSON whenever you want,
  backed up automatically every night.
- **A safety lock** so that browsing on a phone cannot change the collection by accident.
- Interface in English and Italian.

No account, no subscription, no telemetry. The only things it asks the internet for are
the card data and images from Scryfall and, when you open the Administration page,
whether GitHub has a newer version of the program (this can be switched off).

## Quick start

### Windows

1. Download `magic-db.exe` from the
   [latest release](https://github.com/ac-me-git/magic-db/releases/latest) and put it in
   a folder of its own, for example `C:\magic-db`.
2. Double-click it. Windows may show a blue "Windows protected your PC" box, because the
   program is not signed: choose **More info**, then **Run anyway**.
3. Your browser opens on `http://127.0.0.1:8080`. The first start downloads the card
   catalog (about 80 MB) and builds it: a few minutes, with progress shown on the page.

Everything magic-db writes goes in a `data` folder next to the program. To stop it, close
its window. To update, open **Administration**: when a newer version is out it says so,
and one button downloads it, swaps it in and restarts. (Or replace `magic-db.exe` by
hand; `data` is kept either way.)

To reach it from other devices on your network, start it with `MAGICDB_HOST=0.0.0.0`
(see [Configuration](#configuration)), for example from a `start.bat` file:

```bat
set MAGICDB_HOST=0.0.0.0
magic-db.exe
```

### Docker

```sh
curl -O https://raw.githubusercontent.com/ac-me-git/magic-db/main/compose.yaml
docker compose up -d
```

Then open `http://localhost:8080`. Data lives in the `magic-db-data` volume. Set `TZ` in
`compose.yaml` to your time zone, so the nightly jobs run at night for you.

To update: `docker compose pull`, then `docker compose up -d`. The Administration page
tells you when there is something to update to.

### From source

You need [uv](https://docs.astral.sh/uv/) (or Python 3.12+ with pip).

```sh
git clone https://github.com/ac-me-git/magic-db.git
cd magic-db
uv sync
uv run magicdb
```

## Using it

| Page | What it is for |
|---|---|
| **The logo** (home) | The filter panel: build a search by picking colours, types, sets… |
| **The search box** | On every page. Suggests card names as you type; the arrow on the Search button chooses between all cards and your collection |
| **My collection** | Statistics, and from there: the card list, fast input, locations, import, export |
| **My decks** | Deck lists and how complete each one is |
| **Syntax** (footer) | Every search filter, with examples you can click |
| **Administration** (footer) | Whether a newer version is out (and, on Windows, install it); catalog and backup status, run them now, download a backup |

In the list view, click a column's heading to sort by it; click again to reverse.

**Fast input** is for entering a new set: search for it (`set:xyz`), choose the *Fast
input* view, and every card gets two big boxes, non-foil and foil. Tick a box and that
copy is in your collection — in the location, condition and language chosen above the
list. Untick it to take it back. Fast input records one copy per finish; for more
copies of the same card, use the form on the card's page.

The switch with the padlock, top right, locks the collection and the decks: while it is
on you can look at everything but change nothing.

More in [`docs/`](docs/):
[search syntax](docs/search-syntax.md) ·
[decks](docs/decks.md) ·
[import and export](docs/import-export.md) ·
[backup and restore](docs/backup-restore.md) ·
[development](docs/development.md).

## Configuration

Everything is optional and set with environment variables.

| Variable | Default | Meaning |
|---|---|---|
| `MAGICDB_DATA_DIR` | `./data` (the executable: `data` beside it; Docker: `/data`) | Where the catalog, the collection, cached images and backups are kept |
| `MAGICDB_BACKUP_DIR` | `<data>/backups` | Where backups are written. Point it at a folder that is itself backed up or synced |
| `MAGICDB_HOST` | `127.0.0.1` (Docker: `0.0.0.0`) | Address to listen on. `0.0.0.0` makes it reachable from other devices |
| `MAGICDB_PORT` | `8080` | Port to listen on |
| `MAGICDB_PASSWORD` | *(none)* | If set, every page asks for it (HTTP Basic: any user name, this password) |
| `MAGICDB_UPDATE_HOUR` | `3` | Hour of the nightly catalog update, 0–23, local time |
| `MAGICDB_BACKUP_HOUR` | `4` | Hour of the nightly backup, 0–23, local time |
| `MAGICDB_LANG` | `en` | Interface language (`en` or `it`) for visitors whose browser asks for neither. Each visitor can switch at the bottom of any page |
| `MAGICDB_CURRENCY` | `eur` | `eur` or `usd`, for prices and values |
| `MAGICDB_SCHEDULER` | `on` | `off` stops the automatic nightly update and backup |
| `MAGICDB_UPDATE_CHECK` | `on` | `off` stops the Administration page from asking GitHub whether a newer version is out |
| `MAGICDB_RELEASES_URL` | this project's releases on GitHub | Where to ask for the latest release. Only for a fork that publishes its own |

## Your data, and backups

- The card catalog is disposable: it is rebuilt from Scryfall and never backed up.
- Your collection and decks are one file, `data/collection.db`. Every night magic-db
  saves a copy of it and a CSV of the collection in the backup folder, and keeps the last
  7 days, one per week for 4 weeks and one per month for a year. It also saves a copy
  before every import and before upgrading the database.
- **A backup on the same disk does not protect you from losing the disk.** Set
  `MAGICDB_BACKUP_DIR` to a folder that is synced or backed up elsewhere.

How to restore: [docs/backup-restore.md](docs/backup-restore.md).

## Reaching it from outside your home

magic-db is meant for one household. Do not put it on the open internet as it is: put
something in front of it that decides who gets in — a VPN such as Tailscale or
WireGuard, or an access gateway such as Cloudflare Access — or, at the very least, set
`MAGICDB_PASSWORD` and serve it over HTTPS through a reverse proxy.

Behind a reverse proxy, pass the public host name to the app (`Host`, or
`X-Forwarded-Host`): magic-db refuses changes that do not come from its own pages, and
it recognises its own pages by that name.

The safety lock is not access control. It stops accidents, not people: anyone who can
open the site can switch it off.

## Limits

- Card names, text and images are in English. A copy in another language is recorded on
  the English printing, with its language as an attribute.
- Prices are the latest from Scryfall (Cardmarket for euro, TCGplayer for dollars); there
  is no price history.
- One collection per installation; no user accounts.
- A physical deck box is a *location*; a deck is a *list*. Putting a card in a deck list
  does not move it.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). What is being built and why is in
[SPEC.md](SPEC.md); how it was built, milestone by milestone, in [PLAN.md](PLAN.md).

## Support

magic-db is free and will stay free. If it is useful to you, you can
[buy me a coffee](https://buymeacoffee.com/amtg).

## Legal

magic-db is unofficial Fan Content permitted under the Fan Content Policy.
Not approved/endorsed by Wizards. Portions of the materials used are property of Wizards
of the Coast. ©Wizards of the Coast LLC.

Card data and images are provided by [Scryfall](https://scryfall.com) and downloaded by
each installation; this repository contains no card images and no bulk card data, apart
from a small fixture used by the tests.

Code is released under the [MIT licence](LICENSE).
