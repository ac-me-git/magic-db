"""Light or dark pages: the choice in Administration, the cookie, the favicons (SPEC §7)."""

import re
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magicdb.app import create_app
from magicdb.templating import THEME_COOKIE, asset

STATIC = Path(__file__).parents[1] / "src" / "magicdb" / "static"


def icon(name: str, media: str = "") -> str:
    media = f' media="(prefers-color-scheme: {media})"' if media else ""
    return f'<link rel="icon" type="image/svg+xml" href="{asset(name)}"{media}>'


def choose(client, theme: str):
    return client.post("/admin/theme", data={"theme": theme}, follow_redirects=False)


def chosen(client) -> list[str]:
    """The buttons of Administration's choice that are drawn as the current one."""
    page = client.get("/admin").text
    section = page[page.index('<section class="chart" id="theme">') :]
    section = section[: section.index("</section>")]
    assert section.count('aria-pressed="false"') == 2
    return re.findall(r'name="theme" value="(\w+)" class="primary" aria-pressed="true"', section)


def test_pages_follow_the_device_until_the_visitor_chooses(client):
    page = client.get("/").text
    assert '<html lang="en">' in page and "data-theme" not in page
    # an icon for each, and the browser picks
    assert icon("favicon-light.svg", "light") in page
    assert icon("favicon-dark.svg", "dark") in page
    assert page.count('rel="icon"') == 2
    assert chosen(client) == ["auto"]


def test_the_choice_is_in_administration_and_not_in_the_footer(client):
    page = client.get("/syntax").text
    assert "/theme" not in page and "Dark mode" not in page and "theme-switch" not in page
    assert client.get("/theme/dark", follow_redirects=False).status_code == 404
    admin = client.get("/admin").text
    assert "<h2>Appearance</h2>" in admin
    assert '<form class="actions" method="post" action="/admin/theme">' in admin
    for name, label in (("auto", "Automatic"), ("light", "Light"), ("dark", "Dark")):
        button = rf'<button type="submit" name="theme" value="{name}"[^>]*>{label}</button>'
        assert re.search(button, admin), name


def test_a_choice_is_remembered_and_automatic_forgets_it(client):
    response = choose(client, "dark")
    assert response.status_code == 303 and response.headers["location"] == "/admin#theme"
    cookie = response.headers["set-cookie"]
    assert f"{THEME_COOKIE}=dark" in cookie and "Max-Age=31536000" in cookie
    for address in ("/", "/syntax", "/admin", "/no/such/page"):
        page = client.get(address).text
        assert '<html lang="en" data-theme="dark">' in page, address
        assert icon("favicon-dark.svg") in page and page.count('rel="icon"') == 1
    assert chosen(client) == ["dark"]

    choose(client, "light")
    page = client.get("/").text
    assert '<html lang="en" data-theme="light">' in page
    assert icon("favicon-light.svg") in page and "favicon-dark" not in page
    assert chosen(client) == ["light"]

    response = choose(client, "auto")
    assert response.status_code == 303 and response.headers["location"] == "/admin#theme"
    assert "Max-Age=0" in response.headers["set-cookie"]  # the cookie is thrown away
    page = client.get("/").text
    assert '<html lang="en">' in page and "data-theme" not in page
    assert page.count('rel="icon"') == 2
    assert chosen(client) == ["auto"]


@pytest.mark.parametrize("name", ["blue", "DARK", "", "dark.svg"])
def test_an_unknown_choice_changes_nothing(client, name):
    choose(client, "dark")
    response = choose(client, name)
    assert response.status_code == 303 and "set-cookie" not in response.headers
    assert '<html lang="en" data-theme="dark">' in client.get("/").text


def test_a_cookie_that_is_not_a_choice_follows_the_device(client):
    client.cookies.set(THEME_COOKIE, "sepia")
    page = client.get("/").text
    assert "data-theme" not in page and page.count('rel="icon"') == 2
    assert chosen(client) == ["auto"]


def test_the_choice_can_be_made_while_the_collection_is_locked(client):
    client.post("/lock", data={"state": "on"})
    assert choose(client, "dark").status_code == 303  # it changes pages, not the collection
    assert 'data-theme="dark"' in client.get("/").text


def test_the_choice_in_italian(settings):
    client = TestClient(create_app(replace(settings, lang="it")))
    page = client.get("/admin").text
    assert "<h2>Aspetto</h2>" in page
    for label in ("Automatico", "Chiaro", "Scuro"):
        assert f">{label}</button>" in page


def test_the_icons_are_vendored_and_differ(client):
    pictures = []
    for name in ("favicon-light.svg", "favicon-dark.svg"):
        response = client.get(f"/static/{name}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/svg+xml")
        assert response.text.startswith('<svg xmlns="http://www.w3.org/2000/svg"')
        assert "href" not in response.text and "<script" not in response.text
        pictures.append(response.text)
    assert pictures[0] != pictures[1]


def variables(block: str) -> dict[str, str]:
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", block))


def test_dark_colours_are_the_same_however_they_are_asked_for():
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    light = variables(re.search(r":root \{(.*?)\}", css, re.S).group(1))
    by_device = re.search(
        r"@media \(prefers-color-scheme: dark\) \{\s*"
        r':root:not\(\[data-theme="light"\]\) \{(.*?)\}',
        css,
        re.S,
    ).group(1)
    by_choice = re.search(r':root\[data-theme="dark"\] \{(.*?)\}', css, re.S).group(1)
    assert variables(by_device) == variables(by_choice)
    assert set(variables(by_choice)) == set(light) and len(light) >= 10
    assert variables(by_choice) != light
    # the browser's own controls and scroll bars follow the choice too
    assert "color-scheme: dark;" in by_choice and "color-scheme" not in by_device
    assert ':root[data-theme="light"] {\n  color-scheme: light;\n}' in css
