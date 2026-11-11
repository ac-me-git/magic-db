"""Settings read from environment variables (SPEC §3)."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

LANGS = ("en", "it")
CURRENCIES = ("eur", "usd")


class ConfigError(ValueError):
    """An environment variable has a value the app cannot use."""


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    backup_dir: Path
    host: str
    port: int
    password: str
    update_hour: int
    backup_hour: int
    lang: str
    currency: str

    @property
    def collection_path(self) -> Path:
        return self.data_dir / "collection.db"

    @property
    def catalog_dir(self) -> Path:
        return self.data_dir / "catalog"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"


def _int(env: Mapping[str, str], key: str, default: int, low: int, high: int) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{key} must be a whole number, got {raw!r}") from None
    if not low <= value <= high:
        raise ConfigError(f"{key} must be between {low} and {high}, got {value}")
    return value


def _choice(env: Mapping[str, str], key: str, default: str, allowed: tuple[str, ...]) -> str:
    value = env.get(key, "").strip().lower() or default
    if value not in allowed:
        raise ConfigError(f"{key} must be one of {', '.join(allowed)}, got {value!r}")
    return value


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build Settings from ``env`` (defaults to the process environment)."""
    if env is None:
        import os

        env = os.environ
    data_dir = Path(env.get("MAGICDB_DATA_DIR", "").strip() or "data")
    backup_raw = env.get("MAGICDB_BACKUP_DIR", "").strip()
    return Settings(
        data_dir=data_dir,
        backup_dir=Path(backup_raw) if backup_raw else data_dir / "backups",
        host=env.get("MAGICDB_HOST", "").strip() or "127.0.0.1",
        port=_int(env, "MAGICDB_PORT", 8080, 1, 65535),
        password=env.get("MAGICDB_PASSWORD", ""),
        update_hour=_int(env, "MAGICDB_UPDATE_HOUR", 3, 0, 23),
        backup_hour=_int(env, "MAGICDB_BACKUP_HOUR", 4, 0, 23),
        lang=_choice(env, "MAGICDB_LANG", "en", LANGS),
        currency=_choice(env, "MAGICDB_CURRENCY", "eur", CURRENCIES),
    )
