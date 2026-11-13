"""Interface language: per visitor, chosen with the selector (SPEC §7)."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from magicdb.app import create_app
from magicdb.templating import (
    LANG_COOKIE,
    _accepted,
    format_count,
    format_money,
    safe_next,
)

ENGLISH, ITALIAN = "Every Magic card", "Tutte le carte di Magic"


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings), follow_redirects=False)


def test_default_language_comes_from_settings(client, settings):
    assert ENGLISH in client.get("/").text
    italian = TestClient(create_app(replace(settings, lang="it")))
    assert ITALIAN in italian.get("/").text


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7", ITALIAN),
        ("it", ITALIAN),
        ("en-GB,en;q=0.9,it;q=0.8", ENGLISH),
        ("de-DE,de;q=0.9,it;q=0.5,en;q=0.4", ITALIAN),  # first one we speak
        ("en;q=0.2, it;q=0.9", ITALIAN),  # by weight, not by position
        ("it;q=0, en", ENGLISH),  # q=0 means "not this one"
        ("de-DE,fr;q=0.8", ENGLISH),  # nothing we speak: the default
        ("", ENGLISH),
        ("*", ENGLISH),
        (";;q=abc,,,", ENGLISH),
    ],
)
def test_browser_language(client, header, expected):
    assert expected in client.get("/", headers={"Accept-Language": header}).text


def test_selector_sets_a_cookie_and_goes_back(client):
    response = client.get("/lang/it?next=/syntax")
    assert response.status_code == 303
    assert response.headers["location"] == "/syntax"
    cookie = response.headers["set-cookie"]
    assert f"{LANG_COOKIE}=it" in cookie and "Max-Age=31536000" in cookie
    assert "SameSite=lax" in cookie and "Path=/" in cookie

    # the test client keeps the cookie, like a browser
    assert ITALIAN in client.get("/").text
    assert "Sintassi di ricerca" in client.get("/syntax").text
    # the choice wins over the browser's language
    assert ITALIAN in client.get("/", headers={"Accept-Language": "en"}).text

    client.get("/lang/en")
    assert ENGLISH in client.get("/", headers={"Accept-Language": "it"}).text


def test_unknown_language_changes_nothing(client):
    response = client.get("/lang/xx?next=/syntax")
    assert response.status_code == 303 and "set-cookie" not in response.headers
    client.cookies.set(LANG_COOKIE, "klingon")
    assert ENGLISH in client.get("/").text


def test_every_page_offers_the_other_language(client):
    home = client.get("/").text
    assert '<html lang="en">' in home
    assert "<strong>English</strong>" in home
    assert '<a href="/lang/it?next=/" hreflang="it" lang="it">Italiano</a>' in home
    # the link brings you back to the same page, query included
    syntax = client.get("/syntax?x=1&y=two words").text
    assert 'href="/lang/it?next=/syntax%3Fx%3D1%26y%3Dtwo%2520words"' in syntax
    client.get("/lang/it")
    assert "<strong>Italiano</strong>" in client.get("/").text


@pytest.mark.parametrize(
    "target", ["https://evil.example/", "//evil.example/", "/\\evil.example", "evil", "", None]
)
def test_redirect_stays_on_this_site(client, target):
    assert safe_next(target) == "/"
    if target is not None:
        response = client.get("/lang/it", params={"next": target})
        assert response.headers["location"] == "/"


def test_safe_next_accepts_local_paths():
    assert safe_next("/collection?location=3#e5") == "/collection?location=3#e5"
    assert safe_next("nope", "/add") == "/add"


def test_accept_language_parsing():
    assert _accepted("it-IT,it;q=0.9,en;q=0.8") == ["it", "it", "en"]
    assert _accepted("en;q=0.2, IT;q=0.9") == ["it", "en"]
    assert _accepted("") == []


def test_numbers_follow_the_language():
    assert format_count(120054, "en") == "120,054"
    assert format_count(120054, "it") == "120.054"
    assert format_money(1234.5, "eur", "en") == "€1,234.50"
    assert format_money(1234.5, "eur", "it") == "€1.234,50"
    assert format_money(0.5, "usd", "en") == "$0.50"
    assert format_money(None, "eur", "it") == "—"


def test_numbers_on_the_page_follow_the_visitor(catalog, fixture_cards):
    client = TestClient(create_app(catalog))
    lotus = next(c for c in fixture_cards if c["name"] == "Black Lotus")
    usd_free = client.get(f"/card/{lotus['id']}").text
    client.get("/lang/it")
    italian = client.get(f"/card/{lotus['id']}").text
    assert "Tutte le stampe" in italian and "All printings" in usd_free


def test_footer_links_to_the_coffee_page_in_both_languages(client):
    link = 'href="https://buymeacoffee.com/amtg"'
    english = client.get("/").text
    assert link in english and "Buy me a coffee" in english
    italian = client.get("/", headers={"Accept-Language": "it"}).text
    assert link in italian and "Offrimi un caffè" in italian


def test_footer_breaks_the_legal_notice_before_the_copyright_line(client):
    english = client.get("/").text
    assert "Not approved/endorsed by Wizards.<br>Portions of the materials" in english
    italian = client.get("/", headers={"Accept-Language": "it"}).text
    assert "né sostenuto da Wizards.<br>Parte dei materiali" in italian
