-- Things the owner switches from the pages and that must survive a restart
-- (first of all the safety lock, SPEC §7).

CREATE TABLE settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
