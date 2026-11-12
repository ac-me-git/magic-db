-- Decks: lists of cards to build, separate from the collection (SPEC §14).
-- A deck names cards, not printings: any printing owned satisfies it.

CREATE TABLE decks (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  format TEXT,
  notes TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE deck_cards (
  id INTEGER PRIMARY KEY,
  deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
  oracle_id TEXT NOT NULL,
  name TEXT NOT NULL,           -- kept so a deck reads without the catalog
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  board TEXT NOT NULL DEFAULT 'main' CHECK (board IN ('main', 'side')),
  UNIQUE (deck_id, oracle_id, board)
);
CREATE INDEX ix_deck_cards_oracle ON deck_cards(oracle_id);
