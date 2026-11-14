"""Light or dark pages: the choice in the menu, the cookie, the favicons (SPEC §7)."""

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


def choose(client, theme: str, next: str = "/syntax"):
    return client.post("/theme", data={"theme": theme, "next": next}, follow_redirects=False)


def theme_choice(page: str) -> str:
    """The menu's form for light or dark pages."""
    return page.split('<form class="menu-row" method="post" action="/theme">')[1].split("</form>")[
        0
    ]


def chosen(client, address: str = "/syntax") -> list[str]:
    """The buttons of the menu's choice that are drawn as the current one."""
    form = theme_choice(client.get(address).text)
    assert form.count('aria-pressed="false"') == 2
    return re.findall(r'name="theme" value="(\w+)" aria-pressed="true"', form)


def test_pages_follow_the_device_until_the_visitor_chooses(client):
    page = client.get("/").text
    assert '<html lang="en">' in page and "data-theme" not in page
    # an icon for each, and the browser picks
    assert icon("favicon-light.svg", "light") in page
    assert icon("favicon-dark.svg", "dark") in page
    assert page.count('rel="icon"') == 2
    assert chosen(client) == ["auto"]


def test_the_choice_is_in_the_menu_and_not_in_administration(client):
    form = theme_choice(client.get("/syntax?x=1").text)
    assert '<input type="hidden" name="next" value="/syntax?x=1">' in form
    assert "<span>Appearance</span>" in form
    buttons = re.findall(
        r'<button type="submit" name="theme" value="(\w+)"[^>]*>(\w+)</button>', form
    )
    assert buttons == [("light", "Light"), ("dark", "Dark"), ("auto", "Auto")]
    admin = client.get("/admin").text
    assert 'id="theme"' not in admin and "<h2>Appearance</h2>" not in admin
    assert "/theme" not in admin.split("<main>")[1]
    assert client.post("/admin/theme", data={"theme": "dark"}).status_code in (404, 405)
    assert client.get("/theme/dark", follow_redirects=False).status_code == 404


@pytest.mark.parametrize(
    "target", ["https://evil.example/", "//evil.example/", "/\\evil.example", "evil", ""]
)
def test_the_choice_goes_back_only_to_this_site(client, target):
    assert choose(client, "dark", next=target).headers["location"] == "/"


def test_a_choice_is_remembered_and_automatic_forgets_it(client):
    response = choose(client, "dark", next="/decks?sort=name")
    assert response.status_code == 303 and response.headers["location"] == "/decks?sort=name"
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
    assert response.status_code == 303 and response.headers["location"] == "/syntax"
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
    form = theme_choice(client.get("/").text)
    assert "<span>Aspetto</span>" in form
    for label in ("Chiaro", "Scuro", "Auto"):
        assert f">{label}</button>" in form


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
