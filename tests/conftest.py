import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magicdb.app import create_app
from magicdb.config import Settings, load_settings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixture_cards() -> list[dict]:
    return json.loads((FIXTURES / "cards.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def fixture_sets() -> list[dict]:
    return json.loads((FIXTURES / "sets.json").read_text(encoding="utf-8"))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return load_settings({"MAGICDB_DATA_DIR": str(tmp_path / "data")})


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))
