# Changelog

## Unreleased

- The menu on the Search button is now a switch, like the safety lock: *search only my
  collection*. Searching the collection is no longer a separate mode but one more filter:
  the switch writes `haveprint:yes` into the search box, or takes it out, and searches.
  Typing the filter yourself turns the switch on. Addresses saved with
  `scope=collection` still work.

## 1.2.1 — 2026-11-14

- The *Add cards* page is gone. Copies are added from a card's page (the form under
  *My copies*), with fast input, or by importing a file. As before, only printings in
  the Scryfall catalog can be added.
- Fast input is just the two boxes now: the **+** and the copy count beside a ticked box
  are gone. It records one copy per finish; for more copies use the card's page.
- Fast input: quick clicks on both boxes of a card no longer leave the row showing a box
  that is out of date.
- Import from TopDecked keeps the acquired date also for rows that write it as
  `Sat May 03 2025 17:21:51 GMT+0200 (…)`; those cards used to get the day of the import.
- A *Buy me a coffee* link in the footer of every page, on the website and in the README;
  on GitHub it is the repository's Sponsor button (`.github/FUNDING.yml`).

## 1.2.0 — 2026-11-13

- **Fast input**: a third view of the search results with two big boxes, non-foil and
  foil, beside every printing. Ticking a box adds that copy to the collection, in the
  location, condition and language chosen above the list; unticking takes it back.
  Made for entering a new set: `set:xyz`, sorted by number or by name.
- New default colour theme: violet, in light and dark. The colour of the lock switch and
  of the colour bars is unchanged.

## 1.1.0 — 2026-11-13

- The program is now called **AMTG** — Another Magic Tracking Gizmo — in the header and
  page titles. The repository, the program file, the Docker image and the settings keep
  the name `magic-db`.
- Administration says when a newer version has been released. The Windows executable
  can install it with one button: it downloads the new program, checks it against the
  digest GitHub publishes, swaps it in and restarts. Docker and source installations
  are shown the commands to run.
- New settings: `MAGICDB_UPDATE_CHECK=off` stops the check; `MAGICDB_RELEASES_URL`
  points it at a fork's releases.

## 1.0.0 — 2026-11-12

First version.

### Cards

- The complete card catalog from Scryfall bulk data, including previewed cards of
  upcoming sets, rebuilt every night and swapped in without stopping the app.
- Search with a Scryfall-like syntax: names, rules text, type, keyword, colour and colour
  identity, mana value and mana cost, power, toughness, loyalty, rarity, set, collector
  number, format legality, artist, flavour text, year, date, price; `or`, parentheses,
  negation; one result per card or per printing; six sort orders.
- A filter panel that writes the query for you.
- Suggestions of card names while typing; search all cards or only your collection;
  sort the results by clicking a column.
- "Did you mean" for misspelt names.
- Card pages with every printing, prices and legalities; mana and tap symbols as images.
- Card images and symbols downloaded once and served from disk.

### Collection

- Entries with quantity, finish, condition (Cardmarket scale), language, location, notes
  and price paid; identical copies merge.
- Locations (binder, box, deck, other): create, rename, reorder, empty, delete.
- Quick add that remembers finish, condition, language, location and set between cards,
  with undo.
- Move or remove some copies of an entry; an audit trail of every change.
- Badges in search results for what you own; search filters on the collection
  (`have:`, `haveprint:`, `qty`, `loc:`, `cond:`, `finish:`, `lang:`).
- Statistics: totals and value; by colour, rarity, type, mana value, year, finish,
  condition, language and location; set completion; most valuable; recently added.

### Decks

- Deck lists with main deck and sideboard, built by hand or by pasting a list.
- Completion of each deck against the collection, missing cards and their estimated
  cost, and the cards that several decks contend for.
- Statistics of each deck: card types, colours, mana curve, mana symbols in the costs.
- Download a deck as text; `deck:` search filter.

### Your data

- Export to CSV and JSON; import from magic-db, TopDecked, Moxfield, ManaBox and plain
  CSV files, with a preview before anything is written.
- Nightly backup (database copy and CSV) with rotation; a backup before every import
  and database upgrade.
- A safety lock that makes the collection and the decks read-only.

### Running it

- A single Windows executable, and a Docker image for amd64 and arm64.
- Administration page: catalog and backup status, run them now, download backups.
- Optional password; interface in English and Italian, chosen per visitor.
