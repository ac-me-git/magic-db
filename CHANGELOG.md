# Changelog

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
