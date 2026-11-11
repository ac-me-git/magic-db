from dataclasses import replace

from fastapi.testclient import TestClient

from magicdb.app import create_app


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_home_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "magic-db" in response.text
    assert "Every Magic card" in response.text
    assert "Not approved/endorsed by Wizards" in response.text
    assert 'action="/search"' in response.text


def test_home_page_in_italian(settings):
    client = TestClient(create_app(replace(settings, lang="it")))
    response = client.get("/")
    assert '<html lang="it">' in response.text
    assert "Tutte le carte di Magic" in response.text


def test_static_assets_are_vendored(client):
    assert client.get("/static/app.css").status_code == 200
    htmx = client.get("/static/htmx.min.js")
    assert htmx.status_code == 200
    assert "htmx" in htmx.text[:200]
