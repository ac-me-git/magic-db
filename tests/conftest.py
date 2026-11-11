import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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


@pytest.fixture
def catalog(settings: Settings) -> Settings:
    """Settings whose data directory holds a catalog built from the fixture."""
    result = build_fixture_catalog(settings)
    assert result.status == "ok", result.message
    return settings
