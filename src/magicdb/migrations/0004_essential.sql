-- The essential collection (SPEC §5, §14), at the owner's request of 2026-11-15: no
-- locations, no condition, no language; decks without format or sideboard.
--
-- Copies that differed only by condition, language or location become one row per
-- printing and finish: quantities are added, the earliest "added on" date is kept, notes
-- are joined, and the first purchase price found is kept. The lowest id survives.
-- Sideboard copies join the deck's list. The migration runner backs the file up first
-- (a "pre-migration-" backup).

CREATE TABLE entries_new (
  id INTEGER PRIMARY KEY,
  scryfall_id TEXT NOT NULL,
  -- denormalised copy, so the collection stays readable without the catalog
  oracle_id TEXT NOT NULL, name TEXT NOT NULL,
  set_code TEXT NOT NULL, collector_number TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  finish TEXT NOT NULL DEFAULT 'nonfoil' CHECK (finish IN ('nonfoil','foil','etched')),
  notes TEXT, purchase_price REAL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

INSERT INTO entries_new (id, scryfall_id, oracle_id, name, set_code, collector_number,
                         quantity, finish, notes, purchase_price, created_at, updated_at)
SELECT MIN(id), scryfall_id, MIN(oracle_id), MIN(name), MIN(set_code), MIN(collector_number),
       SUM(quantity), finish,
       NULLIF(SUBSTR(GROUP_CONCAT(NULLIF(TRIM(notes), ''), '; '), 1, 500), ''),
       (SELECT e2.purchase_price FROM entries e2
         WHERE e2.scryfall_id = e.scryfall_id AND e2.finish = e.finish
           AND e2.purchase_price IS NOT NULL ORDER BY e2.id LIMIT 1),
       MIN(created_at), MAX(updated_at)
FROM entries e
GROUP BY scryfall_id, finish;

DROP TABLE entries;
ALTER TABLE entries_new RENAME TO entries;
CREATE UNIQUE INDEX ux_entries ON entries (scryfall_id, finish);
CREATE INDEX ix_entries_oracle ON entries(oracle_id);

DROP TABLE locations;

CREATE TABLE decks_new (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  notes TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
INSERT INTO decks_new (id, name, notes, created_at, updated_at)
SELECT id, name, notes, created_at, updated_at FROM decks;

CREATE TABLE deck_cards_new (
  id INTEGER PRIMARY KEY,
  deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
  oracle_id TEXT NOT NULL,
  name TEXT NOT NULL,           -- kept so a deck reads without the catalog
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  UNIQUE (deck_id, oracle_id)
);
INSERT INTO deck_cards_new (id, deck_id, oracle_id, name, quantity)
SELECT MIN(id), deck_id, oracle_id, MIN(name), MIN(SUM(quantity), 999)
FROM deck_cards GROUP BY deck_id, oracle_id;

DROP TABLE deck_cards;
DROP TABLE decks;
ALTER TABLE decks_new RENAME TO decks;
ALTER TABLE deck_cards_new RENAME TO deck_cards;
CREATE INDEX ix_deck_cards_oracle ON deck_cards(oracle_id);
