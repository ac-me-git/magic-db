-- Card catalog (SPEC §4.3). Tables only: indexes are created after the bulk insert.

CREATE TABLE printings (
  id               TEXT PRIMARY KEY,
  oracle_id        TEXT NOT NULL,
  name             TEXT NOT NULL,
  name_norm        TEXT NOT NULL,
  layout           TEXT NOT NULL,
  mana_cost        TEXT,
  cmc              REAL NOT NULL DEFAULT 0,
  type_line        TEXT NOT NULL DEFAULT '',
  type_norm        TEXT NOT NULL DEFAULT '',
  oracle_text      TEXT NOT NULL DEFAULT '',
  oracle_norm      TEXT NOT NULL DEFAULT '',
  flavor_text      TEXT,
  power            TEXT, toughness TEXT, loyalty TEXT, defense TEXT,
  power_num        REAL, toughness_num REAL, loyalty_num REAL,
  colors           INTEGER NOT NULL DEFAULT 0,
  color_identity   INTEGER NOT NULL DEFAULT 0,
  color_count      INTEGER NOT NULL DEFAULT 0,
  keywords         TEXT NOT NULL DEFAULT '|',
  legal_in         TEXT NOT NULL DEFAULT '|',
  set_code         TEXT NOT NULL,
  set_name         TEXT NOT NULL,
  set_type         TEXT NOT NULL,
  collector_number TEXT NOT NULL,
  collector_sort   INTEGER NOT NULL DEFAULT 0,
  rarity           TEXT NOT NULL,
  rarity_ord       INTEGER NOT NULL,
  released_at      TEXT NOT NULL,
  year             INTEGER NOT NULL,
  artist           TEXT,
  finishes         TEXT NOT NULL DEFAULT '|',
  is_digital       INTEGER NOT NULL DEFAULT 0,
  is_promo         INTEGER NOT NULL DEFAULT 0,
  is_reprint       INTEGER NOT NULL DEFAULT 0,
  is_extra         INTEGER NOT NULL DEFAULT 0,
  price_eur        REAL, price_eur_foil REAL,
  price_usd        REAL, price_usd_foil REAL, price_usd_etched REAL,
  img_small        TEXT, img_normal TEXT,
  img_back_small   TEXT, img_back_normal TEXT,
  scryfall_uri     TEXT,
  faces_json       TEXT,
  pref             INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE sets (
  code TEXT PRIMARY KEY, name TEXT NOT NULL, set_type TEXT NOT NULL,
  released_at TEXT, card_count INTEGER NOT NULL DEFAULT 0,
  is_digital INTEGER NOT NULL DEFAULT 0, parent_code TEXT, icon_svg_uri TEXT,
  catalog_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE facets (
  kind TEXT NOT NULL,
  value TEXT NOT NULL, n INTEGER NOT NULL,
  PRIMARY KEY (kind, value)
);

CREATE TABLE names (name TEXT PRIMARY KEY, name_norm TEXT NOT NULL);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
