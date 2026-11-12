# magic-db — Specification

Self-hosted web app to browse every Magic: The Gathering card and track a personal
collection. Runs on a Windows PC with minimal extra software, or in a Linux Docker
container. Open source (MIT).

This document is the source of truth for *what* to build. `PLAN.md` says in which
order. When the two disagree, this file wins. Do not change the database schema or the
search grammar without the owner's approval.

---

## 1. Principles

1. **Two databases, two destinies.** The card catalog is disposable and rebuilt from
   Scryfall. The collection is precious, small, and the only thing that is backed up.
2. **No build chain.** Server-rendered HTML. No Node, no bundler, no CDN at runtime.
3. **One process.** Web server, nightly catalog update and backups all live in the same
   Python process. No cron, no external scheduler, no separate database server.
4. **One search engine.** The filter panel only *writes a query string*; all searching
   goes through the same parser.
5. **Works offline** once the catalog is downloaded (except card images not yet cached).

## 2. Stack

| Area | Choice |
|---|---|
| Language | Python 3.12 |
| Web | FastAPI + Uvicorn, sync `def` endpoints |
| Templates | Jinja2 + HTMX (single vendored file in `static/`) |
| CSS | One hand-written stylesheet, mobile-first, light/dark via `prefers-color-scheme` |
| Storage | SQLite through the standard `sqlite3` module. **No ORM.** |
| HTTP client | `httpx` |
| JSON streaming | `ijson` |
| Fuzzy names | `rapidfuzz` |
| Uploads | `python-multipart` |
| Dev | `pytest`, `ruff`, `uv` for environments |

No other runtime dependency without the owner's approval.

### Layout

```
magic-db/
  src/magicdb/
    app.py            FastAPI app factory, middleware, routes registration
    config.py         settings from environment
    db.py             connections, ATTACH, collection migrations
    catalog/          ingest.py, schema.sql, publish.py
    search/           tokenizer.py, parser.py, compiler.py (AST -> SQL)
    collection/       service.py, importers/, exporters.py
    stats.py
    jobs.py           in-process scheduler, backup, rotation
    images.py         image cache proxy
    i18n/             en.json, it.json
    migrations/       0001_init.sql, 0002_... (collection.db only; inside the package
                      so that the wheel and the Windows executable carry them)
    templates/  static/
  tests/  tests/fixtures/
  scripts/make_fixture.py
  docs/
  Dockerfile  compose.yaml  pyproject.toml  README.md  LICENSE
```

## 3. Configuration

Environment variables, all optional.

| Variable | Default | Meaning |
|---|---|---|
| `MAGICDB_DATA_DIR` | `./data` | catalog, collection, image cache |
| `MAGICDB_BACKUP_DIR` | `<data>/backups` | where backups are written |
| `MAGICDB_HOST` | `127.0.0.1` (Docker image: `0.0.0.0`) | bind address |
| `MAGICDB_PORT` | `8080` | |
| `MAGICDB_PASSWORD` | empty | if set, HTTP Basic auth on every route except `/healthz` |
| `MAGICDB_UPDATE_HOUR` | `3` | local hour of the nightly catalog update |
| `MAGICDB_BACKUP_HOUR` | `4` | local hour of the nightly backup |
| `MAGICDB_LANG` | `en` | UI language (`en` or `it`) for visitors whose browser asks for neither |
| `MAGICDB_CURRENCY` | `eur` | `eur` or `usd`, for values and price sorting |
| `MAGICDB_SCHEDULER` | `on` | `off` stops the automatic catalog update and backup (run them from `/admin` or the command line instead) |

Data directory:

```
data/
  collection.db
  catalog/cards-YYYYMMDDTHHMMSS.db     newest valid file is the live catalog
  images/<size>/<aa>/<scryfall_id>-<face>-<version>.jpg
  symbols/<CODE>.svg
  backups/
  tmp/
```

## 4. Card catalog

### 4.1 Source

Scryfall bulk data. Every request to `api.scryfall.com` sends
`User-Agent: magic-db/<version>` and `Accept: application/json`.

- `GET https://api.scryfall.com/bulk-data` → take the entry with `type == "default_cards"`;
  use its `updated_at` and its download link: `jsonl_download_uri` (gzipped JSON Lines,
  one card per line — what Scryfall serves as of November 2026), falling back to the
  older `download_uri` (one JSON array). The reader recognises gzip and array-vs-lines
  from the file content, never from its name.
- `GET https://api.scryfall.com/sets` → all sets.

`default_cards` holds one object per printing, in English (or the printed language when
no English printing exists), including previewed cards of upcoming sets and current
prices. Never call the per-card API in a loop.

### 4.2 Ingest algorithm

1. Read `bulk-data`. If `updated_at` equals the value stored in the live catalog's
   `meta`, stop ("already up to date").
2. Stream-download the file to `data/tmp/`. Do not load it in memory.
3. Create `data/tmp/cards-<timestamp>.db` with the schema below, **without indexes**.
4. Parse as a stream — line by line for JSON Lines, `ijson.items(f, "item")` for an
   array — and insert in batches of 5,000 with `executemany` inside one transaction.
5. Insert sets. Compute `pref` and `facets`. Create indexes. `ANALYZE`.
6. Sanity check: `PRAGMA integrity_check` is `ok`, row count > 50,000 and
   ≥ 90% of the live catalog's row count. On failure delete the temp file, record the
   error in `col.job_runs`, keep the old catalog.
7. Publish: move the file into `data/catalog/`. New connections pick the newest file by
   name. Older catalog files are deleted on a best-effort basis (ignore "file in use"
   errors; retry on the next run and at startup). **Never overwrite or delete the file
   currently in use** — this is what makes the swap work on Windows.

Target: full ingest in under 5 minutes and under 1 GB RAM on a mini PC.

### 4.3 Schema (catalog file)

```sql
CREATE TABLE printings (
  id               TEXT PRIMARY KEY,   -- Scryfall id of the printing
  oracle_id        TEXT NOT NULL,      -- top-level, else card_faces[0].oracle_id, else id
  name             TEXT NOT NULL,
  name_norm        TEXT NOT NULL,      -- lowercase, accents stripped
  layout           TEXT NOT NULL,
  mana_cost        TEXT,               -- "{2}{R}{R}"; faces joined with " // "
  cmc              REAL NOT NULL DEFAULT 0,
  type_line        TEXT NOT NULL DEFAULT '',
  type_norm        TEXT NOT NULL DEFAULT '',   -- lowercase
  oracle_text      TEXT NOT NULL DEFAULT '',   -- faces joined with "\n//\n"
  oracle_norm      TEXT NOT NULL DEFAULT '',   -- lowercase
  flavor_text      TEXT,
  power            TEXT, toughness TEXT, loyalty TEXT, defense TEXT,
  power_num        REAL, toughness_num REAL, loyalty_num REAL,  -- NULL when not numeric
  colors           INTEGER NOT NULL DEFAULT 0, -- bitmask W=1 U=2 B=4 R=8 G=16
  color_identity   INTEGER NOT NULL DEFAULT 0, -- same bitmask
  color_count      INTEGER NOT NULL DEFAULT 0,
  keywords         TEXT NOT NULL DEFAULT '|',  -- "|flying|trample|" lowercase
  legal_in         TEXT NOT NULL DEFAULT '|',  -- formats where legal or restricted
  set_code         TEXT NOT NULL,
  set_name         TEXT NOT NULL,
  set_type         TEXT NOT NULL,
  collector_number TEXT NOT NULL,
  collector_sort   INTEGER NOT NULL DEFAULT 0, -- leading digits of collector_number
  rarity           TEXT NOT NULL,
  rarity_ord       INTEGER NOT NULL,   -- common 0, uncommon 1, rare 2, special 3, mythic 4, bonus 5
  released_at      TEXT NOT NULL,      -- YYYY-MM-DD
  year             INTEGER NOT NULL,
  artist           TEXT,
  finishes         TEXT NOT NULL DEFAULT '|',  -- "|nonfoil|foil|etched|"
  is_digital       INTEGER NOT NULL DEFAULT 0,
  is_promo         INTEGER NOT NULL DEFAULT 0,
  is_reprint       INTEGER NOT NULL DEFAULT 0,
  is_extra         INTEGER NOT NULL DEFAULT 0, -- see below
  price_eur        REAL, price_eur_foil REAL,
  price_usd        REAL, price_usd_foil REAL, price_usd_etched REAL,
  img_small        TEXT, img_normal TEXT,           -- front face
  img_back_small   TEXT, img_back_normal TEXT,      -- back face, if any
  scryfall_uri     TEXT,
  faces_json       TEXT,               -- raw card_faces, for the detail page
  pref             INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX ix_p_oracle  ON printings(oracle_id, pref);
CREATE INDEX ix_p_name    ON printings(name_norm);
CREATE INDEX ix_p_set     ON printings(set_code, collector_sort);
CREATE INDEX ix_p_cmc     ON printings(cmc);
CREATE INDEX ix_p_release ON printings(released_at);

CREATE TABLE sets (
  code TEXT PRIMARY KEY, name TEXT NOT NULL, set_type TEXT NOT NULL,
  released_at TEXT, card_count INTEGER NOT NULL DEFAULT 0,
  is_digital INTEGER NOT NULL DEFAULT 0, parent_code TEXT, icon_svg_uri TEXT,
  catalog_count INTEGER NOT NULL DEFAULT 0   -- distinct collector numbers present in printings
);

CREATE TABLE facets (          -- feeds the filter panel
  kind TEXT NOT NULL,          -- 'supertype' | 'type' | 'subtype' | 'keyword' | 'format'
  value TEXT NOT NULL, n INTEGER NOT NULL,
  PRIMARY KEY (kind, value)
);

CREATE TABLE names (name TEXT PRIMARY KEY, name_norm TEXT NOT NULL);  -- distinct, non-extra

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
-- keys: schema_version, bulk_updated_at, built_at, row_count
```

Mapping rules:

- **Multi-faced cards.** If a top-level field is missing, build it from `card_faces`:
  `colors` = union of the faces; `mana_cost`, `type_line` joined with ` // `;
  `oracle_text` joined with `\n//\n`; `power`/`toughness`/`loyalty`/`defense`/`cmc`
  from the first face (reversible cards have no top-level mana value); images from `card_faces[0].image_uris` (front) and `card_faces[1].image_uris`
  (back). When the top-level `image_uris` exists (split, adventure, flip) there is no
  back image.
- **`is_extra`** = 1 when `layout` is one of `token`, `double_faced_token`, `emblem`,
  `art_series`, `planar`, `scheme`, `vanguard`, or `set_type` is `memorabilia`,
  `token` or `minigame`.
- **`pref`** picks the printing shown when a card has many:
  `score*100000000 + YYYYMMDD(released_at)` with
  `score = 8*(not digital) + 4*(not promo) + 2*(border_color != 'borderless' and not full_art and no 'showcase'/'extendedart' in frame_effects) + 1*(image_status == 'highres_scan')`.
- **`facets`**: split `type_line` of each face on ` — `; words on the left are
  supertypes (`Basic`, `Legendary`, `Snow`, `World`, `Ongoing`, `Host`) or types; words
  on the right are subtypes. Count over distinct `oracle_id`, non-extra only.
- `legal_in` contains a format when its legality is `legal` or `restricted`.

## 5. Collection database (`collection.db`)

WAL mode, `foreign_keys=ON`. Schema changes only through numbered files in
`src/magicdb/migrations/`, applied at startup and tracked with `PRAGMA user_version`. A backup is
taken automatically before applying any migration.

```sql
CREATE TABLE locations (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  kind TEXT NOT NULL DEFAULT 'other' CHECK (kind IN ('binder','box','deck','other')),
  notes TEXT, sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE entries (
  id INTEGER PRIMARY KEY,
  scryfall_id TEXT NOT NULL,
  -- denormalised copy, so the collection stays readable without the catalog
  oracle_id TEXT NOT NULL, name TEXT NOT NULL,
  set_code TEXT NOT NULL, collector_number TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  finish TEXT NOT NULL DEFAULT 'nonfoil' CHECK (finish IN ('nonfoil','foil','etched')),
  condition TEXT NOT NULL DEFAULT 'NM' CHECK (condition IN ('MT','NM','EX','GD','LP','PL','PO')),  -- Cardmarket scale
  language TEXT NOT NULL DEFAULT 'en',
  location_id INTEGER REFERENCES locations(id) ON DELETE RESTRICT,   -- NULL = unassigned
  notes TEXT, purchase_price REAL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_entries ON entries
  (scryfall_id, finish, condition, language, COALESCE(location_id, 0));
CREATE INDEX ix_entries_oracle ON entries(oracle_id);
CREATE INDEX ix_entries_loc ON entries(location_id);

CREATE TABLE history (       -- append-only audit trail
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL,
  action TEXT NOT NULL,      -- add | edit | move | remove | import | location
  detail TEXT NOT NULL       -- JSON
);

CREATE TABLE job_runs (
  id INTEGER PRIMARY KEY, job TEXT NOT NULL,     -- 'catalog' | 'backup'
  started_at TEXT NOT NULL, finished_at TEXT,
  status TEXT NOT NULL,                          -- running | ok | skipped | error
  message TEXT
);

-- migration 0002: what the owner switches from the pages (the safety lock)
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
```

Rules:

- **Add**: if a row with the same (printing, finish, condition, language, location)
  exists, increase its quantity; otherwise insert.
- **Edit**: if changing a row makes it collide with another, merge them.
- **Move** *n* copies to another location: decrease the source (delete it at 0), add to
  the destination with the merge rule. One transaction. A whole row with nothing to
  merge into only changes its `location_id`: it keeps its id and its "added on" date.
  Moving to where the copies already are does nothing.
- When rows merge, notes are joined with `; ` (without repeating a note already there)
  and a purchase price given with the new copies replaces the old one.
- Problems the user can cause raise `CollectionError(code, **params)`; pages show
  `t("error." + code)`. All collection SQL lives in `collection/service.py`.
- A location with entries cannot be deleted; the UI offers "move everything to…" first.
- The finish must be one of the printing's `finishes`; otherwise warn but allow.
- Languages offered: `en it fr de es pt ja ko ru zhs zht ph`.
- **Condition** uses the Cardmarket scale, best to worst: `MT` Mint, `NM` Near Mint,
  `EX` Excellent, `GD` Good, `LP` Light Played, `PL` Played, `PO` Poor. The UI always
  shows code and full name together, and lists them in this order (not alphabetically).
- Every write also appends a `history` row in the same transaction.

### Connections

One short-lived connection per request (FastAPI dependency):
open `collection.db`, then
`ATTACH DATABASE 'file:<live catalog>?mode=ro' AS cat` (connect with `uri=True`).
Unqualified table names therefore refer to the collection; catalog tables are
`cat.printings`, `cat.sets`, … If no catalog exists yet the app still starts and shows a
"catalog is being downloaded" page with progress.

## 6. Search

### 6.1 Grammar

```
query  := or
or     := and ( "or" and )*
and    := term+
term   := "-" term | "(" or ")" | filter | word
filter := KEY OP value          OP is one of  :  =  !=  <  <=  >  >=
value  := bare-word | "quoted string"
word   := bare-word | "quoted string"      -- matches the card name
```

Case-insensitive. Hand-written tokenizer and recursive-descent parser producing an AST;
a separate compiler turns the AST into `(where_sql, params)`. **Every value is a bound
parameter** — never interpolate user text into SQL. An unknown key or a bad value is a
`QueryError` with a human-readable message, shown above the results; it is never a 500.

### 6.2 Filters

| Keys | Meaning | Notes |
|---|---|---|
| *(bare word)* | name contains | on `name_norm`; all words must match |
| `name:` `!"..."` | name contains / exact name | |
| `o:` `oracle:` | rules text contains | on `oracle_norm` |
| `t:` `type:` | type line contains | `t:legendary t:dragon` |
| `kw:` `keyword:` | has keyword ability | exact keyword, `kw:"first strike"` |
| `c:` `color:` | colours | see below |
| `id:` `identity:` | colour identity | see below |
| `mv` `cmc` | mana value | numeric, all operators |
| `m:` `mana:` | mana cost contains | `m:{2}{R}{R}` or shorthand `m:2rr` |
| `pow` `tou` `loy` | power / toughness / loyalty | numeric; non-numeric values never match |
| `r:` `rarity:` | rarity | `c u r m`, or full names; `r>=r` uses `rarity_ord` |
| `s:` `e:` `set:` | set code | |
| `cn:` | collector number | |
| `st:` | set type | `st:expansion` |
| `f:` `legal:` | legal in format | `f:modern` |
| `a:` `artist:` | artist contains | |
| `ft:` | flavour text contains | |
| `year` | year of the printing | numeric |
| `date` | release date | `date>=2024-01-01` |
| `eur` `usd` | price | numeric, non-foil price |
| `is:` | `foil nonfoil etched reprint promo digital extra dfc` | `dfc` = has a back image |
| `have:` | `yes`/`no` — I own **any printing** of this card | card level |
| `haveprint:` | `yes`/`no` — I own **this printing** | printing level |
| `qty` | copies owned of this printing | numeric, sum over entries |
| `loc:` | owned in a location whose name contains | |
| `cond:` | owned in this condition | |
| `finish:` | owned in this finish | |
| `lang:` | owned in this language | |
| `deck:` | in a deck whose name contains | card level |
| `unique:` | `cards` (default) or `prints` | directive |
| `order:` | `name mv released rarity price set` | directive, default `name` |
| `dir:` | `asc` / `desc` | directive |
| `include:` | `extras` | directive |

**Colours.** Values are any combination of `w u b r g`, a full name
(`white blue black red green`), `c`/`colorless`, or `m`/`multicolor`. With mask `M`:

| Operator | SQL |
|---|---|
| `c:M`, `c>=M` | `(colors & M) = M` |
| `c=M` | `colors = M` |
| `c<=M` | `(colors & ~M & 31) = 0` |
| `c>M` / `c<M` | as `>=` / `<=` and `colors != M` |
| `c:c` | `colors = 0` |
| `c:m` | `color_count >= 2` |

`id:` works the same on `color_identity`, except that **`id:M` means `id<=M`**
(what fits in a Commander deck of those colours).

**Defaults.** Extras (`is_extra = 1`) are hidden unless `include:extras`, `is:extra`, or
a `set:`/`cn:` filter is present.

### 6.3 Result modes

- `unique:prints` — one row per printing.
- `unique:cards` — one row per `oracle_id`, showing the matching printing with the
  highest `pref`:
  `SELECT p.id, <sort keys>, MAX(p.pref) FROM cat.printings p WHERE … GROUP BY +p.oracle_id`
  (SQLite returns the other columns from the row holding the maximum).

Measured on the real catalog (120k printings), which is why the SQL has this shape:

- The query selects only ids and sort keys, sorted and limited, with
  `COUNT(*) OVER ()` for the total; the full rows of the page are fetched afterwards
  by id. Selecting `p.*` in the grouped query is several times slower.
- `GROUP BY +p.oracle_id` (unary plus) stops SQLite from walking `ix_p_oracle`, which
  costs 650–750 ms against 130–250 ms for a plain scan.
- Numeric range filters use `+column` for the same reason (`mv<=4` through `ix_p_cmc`
  is twice as slow as a scan); equality still uses the index.

A typical search takes 120–280 ms on the dev PC; that is the cost of one table scan.

**Directives** (`unique:`, `order:`, `dir:`, `include:`) apply to the whole search
wherever they are written, including inside `or` and parentheses, so that appending
one to any query works. They cannot follow a `-`, and each can be given once.

Collection filters compile to subqueries on `entries`, e.g. `have:yes` →
`p.oracle_id IN (SELECT oracle_id FROM entries)`; `loc:"blue box"` →
`EXISTS (SELECT 1 FROM entries e JOIN locations l ON l.id = e.location_id
WHERE e.scryfall_id = p.id AND l.name LIKE ?)`.

60 results per page. Every result shows a small badge with the owned quantity.

**Fuzzy fallback.** When a query made only of bare words returns nothing, compare the
text against `cat.names` with `rapidfuzz` and show "Did you mean…" with up to 5 links.

### 6.4 Filter panel

A form next to the search box: colour checkboxes with a mode (including / exactly / at
most), colour identity, mana value range, type / subtype / keyword pickers fed by
`cat.facets`, rarity, set, format, power/toughness, "only cards I own", location.
Submitting it **builds a query string** and goes to `/search?q=…`; the generated query
is shown in the search box so the syntax teaches itself. The form is a plain `GET` to
`/filter`, which builds the query (`search/panel.py`) and redirects, so it works without
JavaScript. The panel is on the home page and always starts empty: to refine a search,
edit the text in the search box.

## 7. Pages

| Route | Content |
|---|---|
| `/` | search box, filter panel, catalog status line |
| `/filter?…` | the filter panel's form target: redirects to `/search?q=…` |
| `/search?q=&page=&view=grid\|list` | results |
| `/card/{scryfall_id}` | large image (flip for back face), full text, legalities, prices, all printings, my copies of this card, add form |
| `/add` | **quick add**: name autocomplete → pick printing → Enter. Location, finish, condition and language stay as they were for the next card |
| `/collection` | my entries: filter by location / text, sort, inline edit of quantity / finish / condition / location, move, remove |
| `/locations` | create, rename, reorder, delete; card count and value per location |
| `/stats` | see §9 |
| `/import`, `/export` | see §8 |
| `/admin` | catalog and backup status, last job runs, "update now", "back up now", download latest backup |
| `/syntax` | search syntax reference, generated from the same table the parser uses |
| `/healthz` | `{"status":"ok"}`, no auth |
| `/img/{size}/{scryfall_id}/{face}` | image cache proxy |
| `/api/autocomplete?q=` | up to 15 card names, prefix matches first |

HTMX is used for partial updates (results paging, inline edits, quick add). Every page
must also work as a plain full-page load. Usable on a phone: the quick-add page is the
one most used while sorting physical cards.

**Collection pages.** Every change is a `POST` followed by a redirect to a `next` path
on this site (`safe_next`), so a reload never repeats it. Quick add keeps finish,
condition, language, location and set of the last card in the `magicdb_add` cookie; the
printing preselected for the next card is one from that set when the card has one, else
the catalog's preferred printing. Pressing Enter before a printing is chosen shows the
choices instead of adding. After an add the page confirms what was added and offers
"Undo". HTMX only loads fragments (name suggestions, printings, preview, the edit form
inside a table row); all of it also works as plain page loads. Search results show a
badge with the copies owned: of every printing in card mode, of that printing in print
mode. The card page lists every copy of the card and adds the printing shown.

**Images.** `/img/small|normal/{id}/{front|back}` serves the file from
`data/images/`; on a miss it downloads from the URL stored in the catalog, saves it,
then serves it with a long cache header. If the download fails, return a placeholder
SVG.

**Header.** On every page: the logo (the way to the home page and its filter panel),
the search box, and two links — "My collection", which opens the statistics, and "My
decks". The statistics page is the collection's landing page and links to the card
list, quick add, locations, import and export; quick add is also linked from the
administration page. Syntax and Administration are in the footer.

- **Where to search.** The Search button is a split button. Pressing it (or Enter)
  searches where the last search did; its menu chooses "all cards" or "my collection"
  (`scope=all|collection` in the address, kept by paging, views and sorting). In the
  collection scope a search is limited to the *printings* in the collection
  (`search.run(..., owned_only=True)`), so a card is shown in a printing that is
  owned. The badge with the copies owned is shown in both scopes. The menu is a
  `<details>` with two submit buttons: it needs no JavaScript.
- **Suggestions.** While a name is typed, up to 8 card names appear under the box
  (`GET /search/suggest`, loaded by HTMX), names starting with the text first, the
  typed part in normal weight and the rest in bold. Choosing one searches for exactly
  that card. Nothing is suggested once the text contains filter syntax, and in the
  collection scope only names that are in the collection are offered.
- **Sorting by column.** In the list view the column headers sort the results (name,
  cost, type, set, rarity, price); the header of the order in use shows its direction
  and turns it round. Above the image view there is a line with the same orders plus
  release date. The links rewrite the `order:` and `dir:` of the query
  (`search.with_order`), so the search box always shows the search as it is. `type`
  was added to the orders for this. The collection list's headers sort it too.
- `static/app.js` is the one script the project writes itself: arrow keys and Escape
  in the suggestions, and closing the menus on a click elsewhere. Everything works
  without it.
- Style sheet and scripts are addressed with a fingerprint of their content
  (`templating.asset`), so a browser never keeps an old one after an upgrade.

**Safety lock.** A switch that makes the collection read-only, against accidental
changes (`lock.py`). It is one state for the whole server, saved in the `settings`
table (migration 0002) so that it survives a restart, and mirrored in
`app.state.locked`. A new collection is unlocked.

- While locked, cards and the collection can be searched, browsed, exported, counted
  in the statistics and backed up; the card catalog can be updated. Nothing can change
  the collection: adding, editing, moving, removing, importing, managing locations.
- Enforcement is on the server, in one place: a middleware refuses every request that
  is not a plain read (`GET`, `HEAD`, `OPTIONS`) with status 423 and a page that offers
  to unlock, except `POST /lock` itself and `POST /admin/…`. It denies by default, so a
  page added later that changes the collection is locked without anyone remembering.
- A switch in the header of every page, top right, shows the state and changes it
  (`POST /lock` with `state=on|off` and `next`): a sliding toggle with a padlock on its
  knob, closed and amber when locked, open and grey when unlocked. It is a form button
  with `role="switch"`, so it works without JavaScript and reads properly to a screen
  reader. The lock covers the decks as well as the collection. While locked the pages do
  not show the forms and links that would be refused; they show a notice with "Unlock"
  instead, which comes back to the same page.
- The switch is a deliberate click, not a password: it guards against accidents, not
  against people. Use `MAGICDB_PASSWORD` or an access gateway for that.

**Mana and tap symbols.** Wherever a mana cost or rules text is shown, `{2}`, `{R}`,
`{T}`, `{R/W}` … are rendered as small images by the `symbols` template filter
(`symbols.py`). Each image keeps the original `{X}` as `alt` text, so the page still
reads and copies correctly and degrades to plain text if the picture is missing.
`/symbol/{CODE}.svg` serves Scryfall's SVG card symbols
(`https://svgs.scryfall.io/card-symbols/{CODE}.svg`) through the same download-once
disk cache as card images, in `data/symbols/`; the repository contains none of them.
`CODE` is the symbol without braces and slashes (`{2/W}` → `2W`), with `∞` →
`INFINITY` and `½` → `HALF` — checked against all 84 symbols of Scryfall's
`/symbology`. The response carries a `Content-Security-Policy` that forbids scripts.

**Security.** Optional Basic auth (§3). All state-changing routes are `POST` and reject
requests whose `Origin` header, when present, does not match the `Host` (or the
`X-Forwarded-Host` set by a reverse proxy) — a middleware in `app.py`. The app is
meant to sit behind a reverse proxy / access gateway when exposed to the internet; the
README says so.

**i18n.** All UI strings come from `i18n/en.json` and `i18n/it.json` through a `t()`
template function. Card data is always English. The language is chosen **per visitor**:
the choice made with the selector in the footer (`/lang/{code}?next=…`, stored in the
`magicdb_lang` cookie for a year), else the first supported language of the browser's
`Accept-Language`, else `MAGICDB_LANG`. Numbers and prices follow the language. Every
page gets `t`, `lang`, `languages` and `here` from a template context processor
(`templating.py`), so routes never deal with language. Messages of `QueryError` are
English only.

## 8. Import, export, backup

### Export

- **CSV** (`/export/csv`): one row per entry —
  `Name, Set code, Set name, Collector number, Scryfall ID, Quantity, Finish, Condition, Language, Location, Location kind, Notes, Purchase price, Added`.
- **JSON** (`/export/json`): locations, entries and schema version — a complete,
  re-importable dump.

### Import

`/import` accepts a CSV, detects the format from the header row, and works in two steps:
**preview** (nothing written: how many rows matched, are ambiguous, or failed, with a
table) then **confirm**. Confirm takes a backup first, then writes in one transaction.

Formats are *profiles* (a mapping from column names to fields plus value translations)
in `collection/importers/profiles.py`: `native` (the export above, and as a last resort
any CSV with a `Name` column), `topdecked`, `moxfield`, `manabox`. The JSON dump is
recognised by its `magic_db_export` key and also restores empty locations, their notes
and their order.

**TopDecked.** The profile was written from a real 9,385-row binder export.
`tests/fixtures/topdecked-sample.csv` reproduces its structure with cards of the test
fixture (`scripts/make_topdecked_sample.py`). Two quirks, present in every row:

- prices use a decimal comma without quotes, so `€0,15` arrives as two fields and
  everything after it sits one column to the right of its header;
- empty trailing columns are left out.

So the header is not trusted for positions: the first six fields are fixed
(quantity, name, set code, set name, collector number, finish), the Scryfall id is
found by its shape, and the fields after it are read in header order (acquired date,
acquired price, language, …, condition, notes), re-joining split prices. The file has
one row per copy and no location: it is the export of one binder, whose name is in the
file name (`binder-<name>-<uuid>.csv`) and is offered as a new location.

The `moxfield` and `manabox` profiles follow those tools' documented column names and
have **not** been checked against real exports.

**Defaults.** Rows that give no location, condition or language get the ones chosen on
the preview page (`Defaults`).

**Upload.** Between preview and confirm the file waits in `data/tmp/import-<token>.upload`
(random token, forgotten after a day, 20 MB at most). Confirm re-reads it, so the
preview and the import always agree. `service.import_entries` writes everything in one
transaction with one `import` row in `history`; the imported "added on" date is kept,
and when rows merge the earliest date wins.

**Condition translation.** Files from tools that use the US (TCGplayer) scale are
converted on import:

| US scale | Cardmarket |
|---|---|
| Mint | `MT` |
| Near Mint (NM) | `NM` |
| Lightly Played (LP) | `EX` |
| Moderately Played (MP) | `GD` |
| Heavily Played (HP) | `PL` |
| Damaged (DMG) | `PO` |

Beware: `LP` exists in both scales with different meanings. Each profile declares which
scale its source uses; never translate by guessing from the code alone. The `native`
profile does no translation. An unrecognised condition makes the row "failed" in the
preview; it is never silently defaulted.

Each row is resolved to a printing in this order: Scryfall ID → set code + collector
number → name + set code → name + set name → name alone (only if the card has a single
printing; otherwise "ambiguous"). Refinements: a set + number that points to a card
with a different name is not trusted (the name decides); when name + set finds several
versions and there is no number, the catalog's preferred one is taken and the row is
counted as "guessed"; lookups by name ignore digital-only printings; a name matches a
whole card or one face of it. Unresolved rows can be downloaded as a CSV. Import
modes: *add to collection* (default) or *replace collection*.

### Backup

Nightly, and before every import and migration:

1. `sqlite3.Connection.backup()` to `backups/collection-YYYYMMDD-HHMMSS.db`.
2. `PRAGMA integrity_check` on the copy.
3. The CSV export next to it, same base name.

Rotation after each nightly run: keep everything from the last 7 days, the newest per
ISO week for the last 4 weeks, the newest per month for the last 12 months. Files
prefixed `pre-import-` / `pre-migration-` are kept 30 days. Rotation is a pure function
`(list of file dates, today) -> files to delete`, unit-tested.

Restore is manual and documented: stop the app, copy a backup over `collection.db`,
start.

### Scheduler

A daemon thread wakes once a minute. A job is due when the current local hour is ≥ its
configured hour and it has not succeeded today, **or** its last success is more than
36 hours old (covers a PC that was off at night). Jobs never overlap; each run is
recorded in `job_runs`. At first start, with no catalog, the ingest runs immediately.

Details (`jobs.py`):

- `is_due(now, hour, last_success, last_attempt, running)` is a pure function, tested
  with a fake clock. A run that ends `skipped` ("already up to date", "the collection
  is empty") counts as a success: the job looked and there was nothing to do.
- After any attempt a job waits one hour before trying again, so a failing job (no
  network, Scryfall down) does not retry every minute.
- The scheduler starts in the app's lifespan, so only a real server runs it — a test
  client never downloads or backs up by accident. `MAGICDB_SCHEDULER=off` disables it.
- At startup, runs left `running` by a process that died are marked `error: interrupted`.
- The nightly backup is `collection-<stamp>.db` plus `collection-<stamp>.csv`; the copy
  is opened and checked (`integrity_check`, same number of entries) before it counts.
- While a catalog update runs, `ingest.progress` says where it is (checking,
  downloading, building, publishing); the "no catalog yet" page and `/admin` show it
  and reload themselves every 5 seconds with a `<meta refresh>` (no JavaScript).

**`/admin`** shows the live catalog, the backups (each downloadable; only file names
the app gives to backups are served), the last 20 job runs in local time and the
settings, and has "Update the catalog now" and "Back up now" (`POST
/admin/{job}/run`, run in a background thread).

**Password.** `MAGICDB_PASSWORD` turns on HTTP Basic auth for everything except
`/healthz`: any user name, that password, compared in constant time (`app.py`).

## 9. Statistics (`/stats`)

Computed with SQL on entries joined to the catalog; charts are server-rendered
SVG/CSS bars, no JavaScript library. A selector limits everything to one location.

- **Totals**: copies, distinct printings, distinct cards, estimated value.
- **Value**: `quantity × price`, where non-foil uses `price_eur`, foil uses
  `price_eur_foil`, etched uses the foil price (with `usd`, `price_usd_etched`), falling
  back to the non-foil price when missing. Entries with no price are counted and
  reported as "not priced".
- **Breakdowns** (copies and distinct cards): colour (W, U, B, R, G, multicolour,
  colourless), rarity, card type, mana value, **release year of the printing**, finish,
  condition, language, location.
- **Set completion**: for every set with at least one owned card — owned distinct
  collector numbers / `sets.catalog_count`, percentage, progress bar; sortable by name,
  date, percentage; filter by set type; digital-only sets excluded. Each row links to
  the missing cards: `set:<code> haveprint:no unique:prints`.
- **Top 20** most valuable entries.
- **Recently added**: last 20 entries.

How it is built (`stats.py`): one query reads every entry in scope with the catalog
columns it needs, and the totals and breakdowns are counted in Python. Details settled
while building it:

- A card counts under **each** of its types (an Artifact Creature is both); the types
  are those of the front face, supertypes are left out.
- Colour is one of W U B R G for a single colour, multicolour for two or more,
  colourless for none.
- The mana-value chart leaves lands out (they would all sit at 0); 7 and more share a
  bucket.
- Set completion never rounds up: 100 % means complete. With a location selected, a
  set's "missing" link lists what is not in that location (`-loc:"name"`).
- Set completion is two small queries joined in Python. One SQL join of entries,
  printings and sets made SQLite walk all 120k printings: 580 ms against 6 ms on a
  9,000-entry collection. The whole page takes about 0.2 s on that collection.
- Location names cannot contain a double quote (it becomes an apostrophe), because a
  quote cannot be written inside `loc:"…"`.

## 10. Distribution

- **Docker**: `python:3.12-slim`, non-root user, volume `/data`, `HEALTHCHECK` on
  `/healthz`, image published to `ghcr.io` on every tag. `compose.yaml` with one service
  and one volume.
- **Windows**: a single `magic-db.exe` built with PyInstaller by GitHub Actions on every
  tag and attached to the release. Double-click starts the server, keeps `data/` next to
  the executable, and opens the browser. Alternative documented path: `uv run magicdb`.
- **CI**: `ruff check`, `ruff format --check`, `pytest` on Linux and Windows; a second
  job builds the Docker image and starts it (healthy, non-root, pages answer) without
  publishing, so the Dockerfile cannot rot.

How it is built:

- `scripts/build_exe.py` runs PyInstaller on `packaging/launcher.py` (one file, with a
  console window that shows the address and stops the server when closed). The data
  files of the package and the modules that pick their implementation at run time
  (`uvicorn`, `ijson`, `rapidfuzz`, `python_multipart`) are collected explicitly.
- Run as the executable (`sys.frozen`), `cli.py` defaults the data folder to `data`
  beside the executable instead of the current directory, opens the browser 1.5 s
  after starting (`--no-browser` to prevent it), and if it cannot start it says why
  and waits for Enter instead of vanishing.
- `.github/workflows/release.yml`, on a `v*` tag: tests, then the image for
  `linux/amd64` and `linux/arm64` to `ghcr.io/<owner>/magic-db` (`latest`, `X.Y`,
  `X.Y.Z`), and the executable — started once and checked on `/healthz` before it is
  attached to the GitHub release.
- The image runs as user `magicdb` (uid 10001) with `/data` as its only volume, and the
  nightly jobs follow the container's `TZ`.
- The whole package folder goes into the wheel (and so the image) and the executable:
  nothing the app needs at run time may live outside `src/magicdb/`.

## 11. Legal

- MIT licence for the code.
- README and page footer carry the Wizards of the Coast Fan Content Policy notice
  (unofficial, not approved or endorsed by Wizards) and credit Scryfall as data source.
- The repository contains no card images and no bulk card data. The only exception is
  the small test fixture (§12).

## 12. Testing

`scripts/make_fixture.py` downloads a fixed list of printings from Scryfall once and
writes `tests/fixtures/cards.json` (about 300 objects, same shape as the bulk file).
It must include: a transform DFC, a modal DFC, a split card, an adventure, a saga, a
planeswalker, a battle, a card with `*` power, a colourless artifact, a five-colour
card, a hybrid-mana card, an etched-foil printing, a digital-only printing, a token, a
card with at least five printings, and **one complete small set** (Arabian Nights,
`arn`) for the set-completion tests.

Tests build a catalog from the fixture in a temp directory and never touch the network.
Every milestone in `PLAN.md` lists the tests that must exist.

## 13. Out of scope for v1

Price history, semantic / natural-language
search, card scanning by camera, multiple users, non-English card names and images,
trade lists and wishlists.

## 14. Decks

Lists of cards to build, kept apart from the collection (`decks/`). Added at the
owner's request after milestone 8; a physical deck box is still a *location* (§5),
a deck here is a *list*.

```sql
-- migration 0003
CREATE TABLE decks (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  format TEXT, notes TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE deck_cards (
  id INTEGER PRIMARY KEY,
  deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
  oracle_id TEXT NOT NULL, name TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  board TEXT NOT NULL DEFAULT 'main' CHECK (board IN ('main','side')),
  UNIQUE (deck_id, oracle_id, board)
);
```

Rules:

- A deck names **cards, not printings** (`oracle_id`). What is owned of a card is every
  copy in the collection: any printing, finish, condition and location.
- A deck **never changes the collection**, and writes no `history`.
- Cards are added one at a time by exact name (with the same suggestions as quick add)
  or from a card's page, or by **pasting a deck list** (`decks/text.py`): `4 Lightning
  Bolt`, `4x Lightning Bolt`, a bare name for one copy; a trailing `(SET) number`,
  `[SET]`, `*F*` and `#tags` are dropped; `Fire / Ice` is read as `Fire // Ice`. A line
  saying `Sideboard` starts the sideboard and `SB:` puts one line there; `Deck`,
  `Commander`, `Companion` go back to the main deck; `Maybeboard`, `Tokens` and Arena's
  `About` block are skipped; type groups like `Creatures (12)`, comments (`//`, `#`)
  and blank lines are ignored. Blank lines never start the sideboard. Lines that are
  not cards are reported with a "did you mean" and handed back in the box to correct;
  the rest is added. Tokens and other extras are not deck cards.
- **Completion** of a deck = copies it can count on ÷ copies it asks for, both boards
  together, rounded down (100 % means complete). A card in both boards counts its
  copies in the main board first.
- For a card in two or more decks, the copies owned are compared with what **all decks
  together** ask for. A row is *missing* when there are not enough copies for this deck;
  *contested* when there are enough for this deck alone but not for all of them;
  otherwise it is fine. "Missing" wins over "contested".
- `/decks` shows each deck as a tile with a round gauge of its completion, and one list
  of the cards that several decks want and the collection cannot give to all, with the
  copies owned, the copies wanted and which decks want how many. `/decks/{id}` lists
  the deck by board and card type with the state of every row, the estimated cost of
  what is missing (cheapest paper printing of each card), a download as text and a link
  to see the cards as images (`deck:"name"`). A card's page lists the decks it is in.
- `/decks/{id}/stats`, the second tab of a deck, describes the **main deck** (the
  sideboard is counted but not analysed): cards, lands, other cards, average mana
  value, and four bar charts — by card type (a card counts under each of its types),
  by mana value and by colour (both leave lands out: they are colourless and cost
  nothing, and would bury the rest), and the coloured mana symbols in the costs (a
  hybrid symbol counts for both its colours; generic and `X` are not colours). The
  charts are the ones of the collection statistics (`_charts.html`).
- The JSON export carries the decks, and importing it recreates the ones whose name is
  not already there. The CSV export does not.
- Deck names cannot contain a double quote (it becomes an apostrophe), so that
  `deck:"name"` can always be written.