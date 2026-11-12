import json
import shutil
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from magicdb import db
from magicdb.app import create_app
from magicdb.catalog import ingest
from magicdb.config import Settings, load_settings

FIXTURES = Path(__file__).parent / "fixtures"
CARDS_JSON = FIXTURES / "cards.json"
SETS_JSON = FIXTURES / "sets.json"


@pytest.fixture(scope="session")
def fixture_cards() -> list[dict]:
    return json.loads(CARDS_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def fixture_sets() -> list[dict]:
    return json.loads(SETS_JSON.read_text(encoding="utf-8"))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return load_settings({"MAGICDB_DATA_DIR": str(tmp_path / "data")})


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


def build_fixture_catalog(settings: Settings) -> ingest.UpdateResult:
    """Ingest the fixture as if it were the Scryfall bulk file."""
    return ingest.run_update(settings, from_file=CARDS_JSON, sets_file=SETS_JSON, min_rows=1)


@pytest.fixture(scope="session")
def built_catalog(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The fixture catalog, built once per test run."""
    data_dir = tmp_path_factory.mktemp("built") / "data"
    result = build_fixture_catalog(load_settings({"MAGICDB_DATA_DIR": str(data_dir)}))
    assert result.status == "ok", result.message
    return result.path


@pytest.fixture
def catalog(settings: Settings, built_catalog: Path) -> Settings:
    """Settings whose data directory holds its own copy of the fixture catalog."""
    settings.catalog_dir.mkdir(parents=True)
    shutil.copy(built_catalog, settings.catalog_dir / built_catalog.name)
    db.migrate(settings)
    return settings


@pytest.fixture(autouse=True)
def no_release_check(monkeypatch: pytest.MonkeyPatch):
    """No test asks GitHub for the latest release; those that test it supply the answer."""
    from magicdb import selfupdate

    def offline(*args: object, **kwargs: object):
        raise httpx.ConnectError("offline in tests")

    monkeypatch.setattr(selfupdate, "_get", offline)
    monkeypatch.setattr(selfupdate, "_stream", offline)
    monkeypatch.setattr(selfupdate, "state", selfupdate.State())
