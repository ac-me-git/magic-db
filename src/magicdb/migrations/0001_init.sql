-- Collection database (SPEC §5).

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
  condition TEXT NOT NULL DEFAULT 'NM'
    CHECK (condition IN ('MT','NM','EX','GD','LP','PL','PO')),  -- Cardmarket scale
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
