"""Light or dark pages: the button in the footer, the cookie, the favicons (SPEC §7)."""

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


def test_pages_follow_the_device_until_the_visitor_chooses(client):
    page = client.get("/").text
    assert '<html lang="en">' in page and "data-theme" not in page
    # an icon for each, and the browser picks
    assert icon("favicon-light.svg", "light") in page
    assert icon("favicon-dark.svg", "dark") in page
    assert page.count('rel="icon"') == 2


def test_the_button_in_the_footer_switches_and_is_remembered(client):
    page = client.get("/syntax?x=1&y=two words").text
    footer = page[page.index('<footer class="site-footer">') :]
    # both are in the page; the style sheet shows the one that applies
    back = "next=/syntax%3Fx%3D1%26y%3Dtwo%2520words"
    assert f'<a class="button to-dark" href="/theme/dark?{back}">' in footer
    assert f'<a class="button to-light" href="/theme/light?{back}">' in footer
    assert "Dark mode</a>" in footer and "Light mode</a>" in footer

    response = client.get("/theme/dark?next=/syntax", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/syntax"
    cookie = response.headers["set-cookie"]
    assert f"{THEME_COOKIE}=dark" in cookie and "Max-Age=31536000" in cookie
    for address in ("/", "/syntax", "/admin", "/no/such/page"):
        page = client.get(address).text
        assert '<html lang="en" data-theme="dark">' in page, address
        assert icon("favicon-dark.svg") in page and page.count('rel="icon"') == 1

    client.get("/theme/light")
    page = client.get("/").text
    assert '<html lang="en" data-theme="light">' in page
    assert icon("favicon-light.svg") in page and "favicon-dark" not in page


@pytest.mark.parametrize("name", ["blue", "DARK", "auto", "dark.svg"])
def test_unknown_theme_changes_nothing(client, name):
    response = client.get(f"/theme/{name}?next=/syntax", follow_redirects=False)
    assert response.status_code == 303 and "set-cookie" not in response.headers
    client.cookies.set(THEME_COOKIE, name)
    page = client.get("/").text
    assert "data-theme" not in page and page.count('rel="icon"') == 2


@pytest.mark.parametrize("target", ["https://evil.example/", "//evil.example/", "evil", ""])
def test_the_button_never_leaves_this_site(client, target):
    response = client.get("/theme/dark", params={"next": target}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/"


def test_the_button_in_italian(settings):
    client = TestClient(create_app(replace(settings, lang="it")))
    page = client.get("/").text
    assert "Modalità scura</a>" in page and "Modalità chiara</a>" in page


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
    # and the button reads "Light mode" wherever the page is dark
    assert css.count(':root:not([data-theme="light"]) .to-light {\n    display: inline-flex;') == 1
    assert css.count(':root[data-theme="dark"] .to-light {\n  display: inline-flex;') == 1
