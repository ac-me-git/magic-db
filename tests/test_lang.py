"""Interface language: per visitor, chosen in the menu (SPEC §7)."""

import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from markupsafe import Markup

from magicdb.app import create_app
from magicdb.i18n import translator
from magicdb.templating import (
    LANG_COOKIE,
    _accepted,
    format_count,
    format_money,
    safe_next,
)

# what the home page says before there is a catalog, in the two languages
ENGLISH = "The card catalog has not been downloaded yet"
ITALIAN = "Il catalogo delle carte non è ancora stato scaricato"


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


def language_choice(page: str) -> str:
    """The menu's line with the languages."""
    menu = page.split('<div class="menu-panel">')[1].split("</details>")[0]
    return menu.split('<span class="segments">')[1].split("</span>")[0]


def current(choice: str) -> list[str]:
    """The languages drawn as the current one."""
    return re.findall(r'hreflang="(\w+)"[^>]*aria-current="true"', choice)


def test_the_languages_are_in_the_menu_and_not_in_administration(client):
    home = client.get("/").text
    assert '<html lang="en">' in home
    choice = language_choice(home)
    assert (
        '<a href="/lang/en?next=/" hreflang="en" lang="en" title="English" '
        'aria-current="true">ENG</a>'
    ) in choice
    assert '<a href="/lang/it?next=/" hreflang="it" lang="it" title="Italiano">ITA</a>' in choice
    assert current(choice) == ["en"]
    # the link brings you back to the same page, query included
    syntax = language_choice(client.get("/syntax?x=1&y=two words").text)
    assert 'href="/lang/it?next=/syntax%3Fx%3D1%26y%3Dtwo%2520words"' in syntax
    admin = client.get("/admin").text
    assert 'id="language"' not in admin and "<h2>Language</h2>" not in admin
    assert "/lang/" not in admin.split("<main>")[1]

    client.get("/lang/it")
    assert current(language_choice(client.get("/").text)) == ["it"]


def test_until_one_is_chosen_the_browser_language_is_shown_as_chosen(client):
    page = client.get("/", headers={"Accept-Language": "it"}).text
    assert current(language_choice(page)) == ["it"]


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


def footer(page: str) -> str:
    return page.split('<footer class="site-footer">')[1].split("</footer>")[0]


WIZARDS = '<a href="https://company.wizards.com/" target="_blank" rel="noopener">Wizards</a>'
SCRYFALL = '<a href="https://scryfall.com/" target="_blank" rel="noopener">Scryfall</a>'


def test_footer_links_to_the_coffee_page_in_both_languages(client):
    link = 'href="https://buymeacoffee.com/amtg"'
    english = client.get("/").text
    assert link in english and "Buy me a coffee" in english
    italian = client.get("/", headers={"Accept-Language": "it"}).text
    assert link in italian and "Offrimi un caffè" in italian


def test_footer_breaks_the_legal_notice_before_the_copyright_line(client):
    english = client.get("/").text
    assert f"Not approved/endorsed by {WIZARDS}.<br>Portions of the materials" in english
    italian = client.get("/", headers={"Accept-Language": "it"}).text
    assert f"né sostenuto da {WIZARDS}.<br>Parte dei materiali" in italian


def test_footer_credits_scryfall_on_the_copyright_line(client):
    english = client.get("/").text
    assert f"©Wizards of the Coast LLC. Card data by {SCRYFALL}.</p>" in english
    italian = client.get("/", headers={"Accept-Language": "it"}).text
    assert f"©Wizards of the Coast LLC. Dati delle carte forniti da {SCRYFALL}.</p>" in italian


def test_footer_has_only_the_notice_and_then_the_coffee(client):
    for address in ("/", "/syntax", "/admin"):
        bottom = footer(client.get(address).text)
        assert bottom.count("<p>") == 2, address
        assert bottom.index("Fan Content Policy") < bottom.index("buymeacoffee.com"), address
        assert re.findall(r'href="([^"]+)"', bottom) == [
            "https://company.wizards.com/",
            "https://scryfall.com/",
            "https://buymeacoffee.com/amtg",
        ], address


def test_a_link_in_a_translated_sentence_is_kept_and_the_rest_escaped():
    t = translator("en")
    link = Markup('<a href="/">chips</a>')
    assert t("no.such.key", default="Fish & {x}", x=link) == 'Fish &amp; <a href="/">chips</a>'
    assert isinstance(t("no.such.key", default="{x}", x=link), Markup)
    # a plain value leaves the sentence alone: the template escapes all of it
    assert t("no.such.key", default="Fish & {x}", x="<b>") == "Fish & <b>"
    assert not isinstance(t("no.such.key", default="{x}", x="<b>"), Markup)
