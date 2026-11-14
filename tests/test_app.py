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
    assert ">AMTG</a>" in response.text
    assert 'title="Another Magic Tracking Gizmo"' in response.text
    # the name is in the header only: the page has no title or tagline of its own
    assert "<h1" not in response.text and "Every Magic card" not in response.text
    assert 'Not approved/endorsed by <a href="https://company.wizards.com/"' in response.text
    assert 'action="/search"' in response.text
    # no catalog yet: nothing to search, so no map of the syntax and no filter panel
    assert "The card catalog has not been downloaded yet" in response.text
    assert "Syntax map" not in response.text and 'action="/filter"' not in response.text
    client.post("/admin/home", data={"view": "filters"})  # whichever of the two is chosen
    response = client.get("/")
    assert "The card catalog has not been downloaded yet" in response.text
    assert "Syntax map" not in response.text and 'action="/filter"' not in response.text


def test_home_page_in_italian(settings):
    client = TestClient(create_app(replace(settings, lang="it")))
    response = client.get("/")
    assert '<html lang="it">' in response.text
    assert "Il catalogo delle carte non è ancora stato scaricato" in response.text
    assert "Tutte le carte di Magic" not in response.text


def test_static_assets_are_vendored(client):
    assert client.get("/static/app.css").status_code == 200
    htmx = client.get("/static/htmx.min.js")
    assert htmx.status_code == 200
    assert "htmx" in htmx.text[:200]
