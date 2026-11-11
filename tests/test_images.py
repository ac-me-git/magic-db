"""Image cache proxy (SPEC §7), with a fake Scryfall."""

import httpx
import pytest
from fastapi.testclient import TestClient

from magicdb import images
from magicdb.app import create_app

JPEG = b"\xff\xd8\xff\xe0 pretend this is a picture \xff\xd9"
UNKNOWN = "00000000-0000-4000-8000-000000000000"


class FakeScryfall:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.mode = "ok"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(str(request.url))
        if self.mode == "down":
            raise httpx.ConnectError("no network", request=request)
        if self.mode == "error":
            return httpx.Response(500, content=b"oops", headers={"content-type": "image/jpeg"})
        if self.mode == "html":
            return httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})
        if self.mode == "empty":
            return httpx.Response(200, content=b"", headers={"content-type": "image/jpeg"})
        return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})


@pytest.fixture
def scryfall():
    return FakeScryfall()


@pytest.fixture
def client(catalog, scryfall):
    http = httpx.Client(transport=httpx.MockTransport(scryfall))
    return TestClient(create_app(catalog, http_client=http))


def card_id(fixture_cards, name):
    return next(c["id"] for c in fixture_cards if c["name"].startswith(name))


def cached(settings):
    return sorted(p.name for p in settings.images_dir.rglob("*") if p.is_file())


def test_miss_downloads_then_hit_serves_from_disk(client, scryfall, catalog, fixture_cards):
    bolt = card_id(fixture_cards, "Lightning Bolt")
    first = client.get(f"/img/normal/{bolt}/front")
    assert first.status_code == 200
    assert first.headers["content-type"] == "image/jpeg"
    assert first.content == JPEG
    assert "immutable" in first.headers["cache-control"]
    assert len(scryfall.calls) == 1 and "/normal/front/" in scryfall.calls[0]
    files = cached(catalog)
    assert len(files) == 1 and files[0].startswith(f"{bolt}-front-") and files[0].endswith(".jpg")

    second = client.get(f"/img/normal/{bolt}/front")
    assert second.status_code == 200 and second.content == JPEG
    assert len(scryfall.calls) == 1  # no second download

    small = client.get(f"/img/small/{bolt}/front")
    assert small.status_code == 200
    assert len(scryfall.calls) == 2 and "/small/front/" in scryfall.calls[1]
    assert len(cached(catalog)) == 2


@pytest.mark.parametrize("mode", ["down", "error", "html", "empty"])
def test_failed_download_gives_a_placeholder_and_caches_nothing(
    client, scryfall, catalog, fixture_cards, mode
):
    bolt = card_id(fixture_cards, "Lightning Bolt")
    scryfall.mode = mode
    response = client.get(f"/img/normal/{bolt}/front")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.headers["cache-control"] == "no-store"
    assert response.text.startswith("<svg")
    assert cached(catalog) == []

    scryfall.mode = "ok"  # the next request tries again and succeeds
    assert client.get(f"/img/normal/{bolt}/front").content == JPEG
    assert len(cached(catalog)) == 1


def test_back_face(client, scryfall, fixture_cards):
    delver = card_id(fixture_cards, "Delver of Secrets")
    assert client.get(f"/img/normal/{delver}/back").content == JPEG
    assert "/normal/back/" in scryfall.calls[0]

    bolt = card_id(fixture_cards, "Lightning Bolt")
    response = client.get(f"/img/normal/{bolt}/back")  # there is no back
    assert response.text.startswith("<svg")
    assert len(scryfall.calls) == 1


@pytest.mark.parametrize(
    "path",
    [
        f"/img/huge/{UNKNOWN}/front",
        f"/img/normal/{UNKNOWN}/side",
        f"/img/normal/{UNKNOWN}/front",  # well-formed id, but no such card
        "/img/normal/not-an-id/front",
        "/img/normal/..%2F..%2Fcollection.db/front",
    ],
)
def test_bad_requests_are_not_found(client, scryfall, path):
    assert client.get(path).status_code == 404
    assert scryfall.calls == []


def test_without_a_catalog(settings, scryfall):
    http = httpx.Client(transport=httpx.MockTransport(scryfall))
    client = TestClient(create_app(settings, http_client=http))
    assert client.get(f"/img/normal/{UNKNOWN}/front").text.startswith("<svg")


def test_new_version_replaces_the_old_file(settings, scryfall):
    http = httpx.Client(transport=httpx.MockTransport(scryfall))
    base = "https://cards.scryfall.io/normal/front/a/b/x.jpg"
    old = images.cache_path(settings, "normal", UNKNOWN, "front", base + "?1562900000")
    new = images.cache_path(settings, "normal", UNKNOWN, "front", base + "?1700000000")
    assert old != new and old.parent == new.parent
    assert images.cache_path(settings, "normal", UNKNOWN, "front", base).name.endswith("-0.jpg")

    assert images.download(http, base + "?1562900000", old)
    assert images.download(http, base + "?1700000000", new)
    assert [p.name for p in new.parent.iterdir()] == [new.name]


def test_only_scryfall_hosts_are_fetched(settings, scryfall):
    http = httpx.Client(transport=httpx.MockTransport(scryfall))
    target = settings.images_dir / "x.jpg"
    for url in ["https://example.com/a.jpg", "https://evilscryfall.io/a.jpg", "file:///etc/passwd"]:
        assert not images.download(http, url, target)
    assert scryfall.calls == [] and not target.exists()
