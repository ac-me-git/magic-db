# Backup and restore

## What is backed up

Your collection, locations, decks and settings are one SQLite file: `collection.db`, in
the data folder. That file is the only thing worth backing up. The card catalog and the
cached images are downloaded again whenever they are missing.

## Automatic backups

Every night at `MAGICDB_BACKUP_HOUR` (4:00 by default) magic-db writes, in the backup
folder:

- `collection-YYYYMMDD-HHMMSS.db` — a complete copy of the database, checked before it
  counts as a backup;
- `collection-YYYYMMDD-HHMMSS.csv` — the collection as a spreadsheet, readable without
  magic-db.

If the computer was off at that hour, the backup runs when it is on again. An empty
collection is not backed up.

Old backups are removed automatically. What is kept:

- every backup of the last 7 days;
- the newest of each week, for the last 4 weeks;
- the newest of each month, for the last 12 months.

magic-db also saves a copy before anything risky, kept for 30 days:

- `pre-import-…db` before every import;
- `pre-migration-…db` before upgrading the database to a newer version of the app.

You can make a backup at any moment, and download any of them, from the **Administration**
page.

## Where backups go

By default, in `backups` inside the data folder — on the same disk as the collection.
That protects you from a mistake, not from a broken disk or a stolen computer. Set
`MAGICDB_BACKUP_DIR` to a folder that is synced or backed up somewhere else (a cloud
folder, a NAS):

```sh
# Docker: mount a host folder and point the variable at it (see compose.yaml)
MAGICDB_BACKUP_DIR=/backups
```

```bat
rem Windows
set MAGICDB_BACKUP_DIR=D:\OneDrive\magic-db-backups
magic-db.exe
```

## Restoring

Restoring replaces the whole collection with the one in the backup. It is done by hand,
on purpose.

1. **Stop magic-db** (close its window, or `docker compose stop`).
2. In the data folder, delete `collection.db-wal` and `collection.db-shm` if they are
   there. They belong to the database you are replacing.
3. Copy the backup you want over `collection.db`:

   ```bat
   copy /Y backups\collection-20261112-040000.db collection.db
   ```

   ```sh
   cp backups/collection-20261112-040000.db collection.db
   ```

4. **Start magic-db** again.

With Docker and the default volume:

```sh
docker compose stop
docker compose run --rm --entrypoint sh magic-db -c \
  "rm -f /data/collection.db-wal /data/collection.db-shm && \
   cp /data/backups/collection-20261112-040000.db /data/collection.db"
docker compose start
```

If you only have a CSV or JSON export, start with an empty collection and bring it back
with **Import** — see [import-export.md](import-export.md). The JSON export also restores
empty locations and decks; the CSV does not.

## Moving to another computer

Stop magic-db, copy `collection.db` (and the `backups` folder if you want the history)
into the data folder of the new installation, and start it there. The catalog is
downloaded again by itself.
