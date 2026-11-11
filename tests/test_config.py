from pathlib import Path

import pytest

from magicdb.config import ConfigError, load_settings


def test_defaults():
    s = load_settings({})
    assert s.data_dir == Path("data")
    assert s.backup_dir == Path("data") / "backups"
    assert s.host == "127.0.0.1"
    assert s.port == 8080
    assert s.password == ""
    assert s.update_hour == 3
    assert s.backup_hour == 4
    assert s.lang == "en"
    assert s.currency == "eur"


def test_derived_paths():
    s = load_settings({"MAGICDB_DATA_DIR": "x"})
    assert s.collection_path == Path("x") / "collection.db"
    assert s.catalog_dir == Path("x") / "catalog"
    assert s.images_dir == Path("x") / "images"
    assert s.tmp_dir == Path("x") / "tmp"
    assert s.backup_dir == Path("x") / "backups"


def test_overrides():
    s = load_settings(
        {
            "MAGICDB_DATA_DIR": "d",
            "MAGICDB_BACKUP_DIR": "b",
            "MAGICDB_HOST": "0.0.0.0",
            "MAGICDB_PORT": "9000",
            "MAGICDB_PASSWORD": "secret",
            "MAGICDB_UPDATE_HOUR": "1",
            "MAGICDB_BACKUP_HOUR": "23",
            "MAGICDB_LANG": "IT",
            "MAGICDB_CURRENCY": "usd",
        }
    )
    assert s.backup_dir == Path("b")
    assert (s.host, s.port, s.password) == ("0.0.0.0", 9000, "secret")
    assert (s.update_hour, s.backup_hour) == (1, 23)
    assert (s.lang, s.currency) == ("it", "usd")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("MAGICDB_PORT", "abc"),
        ("MAGICDB_PORT", "70000"),
        ("MAGICDB_UPDATE_HOUR", "24"),
        ("MAGICDB_BACKUP_HOUR", "-1"),
        ("MAGICDB_LANG", "fr"),
        ("MAGICDB_CURRENCY", "gbp"),
    ],
)
def test_bad_values_are_rejected(key, value):
    with pytest.raises(ConfigError, match=key):
        load_settings({key: value})
