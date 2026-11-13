"""Fast input: a box per finish beside every printing of a search (SPEC §7)."""

import re
from contextlib import closing
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from magicdb import db, search
from magicdb.app import create_app
from magicdb.collection import service

HTMX = {"HX-Request": "true"}
UNKNOWN = "00000000-0000-4000-8000-000000000000"


@pytest.fixture
def client(catalog):
    return TestClient(create_app(catalog), follow_redirects=False)


@pytest.fixture
def both(fixture_cards):
    """A paper printing that exists in non-foil and in foil."""
    return next(
        c for c in fixture_cards if set(c["finishes"]) == {"nonfoil", "foil"} and not c["digital"]
    )


@pytest.fixture
def foil_only(fixture_cards):
    return next(c for c in fixture_cards if c["finishes"] == ["foil"])


def entries(catalog):
    with closing(db.connect(catalog)) as conn:
        found = conn.execute(
            "SELECT e.scryfall_id, e.quantity, e.finish, e.condition, e.language, l.name "
            "FROM entries e LEFT JOIN locations l ON l.id = e.location_id ORDER BY e.id"
        )
        return [tuple(row) for row in found]


def toggle(client, card, finish="nonfoil", action="add", **extra):
    data = {"printing": card["id"], "finish": finish, "action": action, **extra}
    return client.post("/fast/toggle", data=data, headers=HTMX)


def box(html, finish=None):
    """A big box: (is ticked, is disabled, next action). With `finish`, the box of that
    finish in a row of the page; without, the only box in a click's answer."""
    if finish is not None:
        html = next(
            part for part in html.split("<form")[1:] if f'name="finish" value="{finish}"' in part
        )
    found = re.search(
        r'<span class="fast-box">\s*<input type="hidden" name="action" value="(\w+)">'
        r'\s*(<button type="submit" class="bigcheck.*?>)',
        html,
        re.S,
    )
    tag = found.group(2)
    return ('aria-checked="true"' in tag, " disabled" in tag, found.group(1))


def page_row(client, card):
    """The row of a printing on the fast input page of its set."""
    page = client.get(f"/search?q=set:{card['set']}&view=input").text
    return next(r for r in page.split("<tr>") if card["id"] in r)


# --- the view -------------------------------------------------------------------------


def test_results_offer_fast_input_as_a_third_view(client):
    page = client.get("/search?q=bolt").text
    assert ">Fast input</a>" in page and "view=input" in page
    assert "bigcheck" not in page  # the boxes are only in that view


def test_input_view_has_two_boxes_per_printing(client, both):
    page = client.get(f"/search?q=set:{both['set']}&view=input").text
    assert 'class="card-list fast-list"' in page
    rows = page.count("<tr>") - 1  # minus the heading
    assert rows >= 1 and page.count('class="bigcheck') == 2 * rows
    assert f'name="printing" value="{both["id"]}"' in page
    assert ">Non-foil</th>" in page and ">Foil</th>" in page
    assert "New copies go to" in page and 'action="/fast/settings"' in page
    assert 'aria-current="true"' in page.split(">Fast input</a>")[0].rsplit("<a", 1)[1]


def test_input_view_always_lists_printings(client, fixture_cards):
    """One row per printing even though a plain search shows one row per card."""
    name = next(
        c["name"]
        for c in fixture_cards
        if sum(o["name"] == c["name"] for o in fixture_cards) > 1 and "//" not in c["name"]
    )
    printings = sum(c["name"] == name for c in fixture_cards)
    page = client.get("/search", params={"q": f'!"{name}"', "view": "input"}).text
    assert page.count("<tr>") - 1 == printings
    assert "1 card" in client.get("/search", params={"q": f'!"{name}"'}).text


def test_search_can_be_forced_to_printings(catalog, fixture_cards):
    with closing(db.connect(catalog)) as conn:
        cards = search.run(conn, "")
        prints = search.run(conn, "", prints=True, per_page=500)
    assert cards.unique == "cards" and prints.unique == "prints"
    assert cards.total < prints.total <= len(fixture_cards)
    assert len(prints.rows) == prints.total > search.PER_PAGE  # a whole set on one page


def test_a_finish_that_was_not_printed_cannot_be_ticked(client, foil_only):
    page = client.get(f"/search?q=set:{foil_only['set']}&view=input").text
    row = next(r for r in page.split("<tr>") if foil_only["id"] in r)
    assert box(row, "nonfoil") == (False, True, "add")
    assert box(row, "foil") == (False, False, "add")
    assert "does not exist in this finish" in row


def test_sorting_and_paging_stay_in_the_view(client, both):
    page = client.get(f"/search?q=set:{both['set']}&view=input").text
    sort_links = re.findall(r'class="sorter[^"]*" href="([^"]+)"', page)
    assert len(sort_links) == 2 and all("view=input" in link for link in sort_links)


def test_fast_opens_the_newest_set(client, catalog):
    answer = client.get("/fast")
    assert answer.status_code == 303
    target = urlsplit(answer.headers["location"])
    query = parse_qs(target.query)
    assert target.path == "/search" and query["view"] == ["input"]
    code = re.fullmatch(r"set:(\w+) order:set", query["q"][0]).group(1)
    with closing(db.connect(catalog)) as conn:
        newest = conn.execute(
            "SELECT code FROM cat.sets WHERE catalog_count > 0 AND is_digital = 0 "
            "AND set_type IN ('expansion','core','masters','draft_innovation','commander') "
            "AND released_at <= date('now') ORDER BY released_at DESC, code LIMIT 1"
        ).fetchone()[0]
    assert code == newest
    assert client.get(answer.headers["location"]).status_code == 200


def test_fast_is_linked_from_the_collection_and_administration(client):
    assert 'href="/fast"' in client.get("/stats").text
    assert 'href="/fast"' in client.get("/admin").text


# --- ticking --------------------------------------------------------------------------


def test_ticking_adds_a_copy_and_unticking_takes_it_back(client, catalog, both):
    answer = toggle(client, both)
    assert answer.status_code == 200
    assert box(answer.text) == (True, False, "remove")
    assert entries(catalog) == [(both["id"], 1, "nonfoil", "NM", "en", None)]

    answer = toggle(client, both, "foil")
    assert box(answer.text) == (True, False, "remove")
    assert "×2</span>" in answer.text  # the badge counts every copy of the printing
    assert [e[1:3] for e in entries(catalog)] == [(1, "nonfoil"), (1, "foil")]

    answer = toggle(client, both, "nonfoil", "remove")
    assert box(answer.text) == (False, False, "add")
    assert entries(catalog) == [(both["id"], 1, "foil", "NM", "en", None)]

    toggle(client, both, "nonfoil", "remove")  # nothing to take back: nothing happens
    assert len(entries(catalog)) == 1


def test_a_click_redraws_only_its_box_and_the_badge(client, both):
    """The rest of the row stays, so a click on the other box that is still waiting in
    the row's queue is not lost, and an answer cannot draw the other box out of date."""
    answer = toggle(client, both, "foil").text
    assert "<tr" not in answer and "<form" not in answer and "<html" not in answer
    assert answer.count('class="bigcheck') == 1 and 'name="finish"' not in answer
    badge = f'<span id="owned-{both["id"]}" hx-swap-oob="true"><span class="owned"'
    assert badge in answer and "×1</span>" in answer
    assert 'hx-swap-oob="true"></span>' in toggle(client, both, "foil", "remove").text


def test_clicks_in_a_row_wait_for_each_other(client, both):
    row = page_row(client, both)
    assert row.count('hx-sync="closest tr:queue all"') == 2
    assert row.count('hx-target="find .fast-box" hx-swap="outerHTML"') == 2
    assert f'<span id="owned-{both["id"]}"></span>' in row  # there for the badge to fill
    # the action lives inside the redrawn box: a queued click sends what the box shows then
    assert box(row, "foil") == (False, False, "add")


def test_a_cell_is_the_box_and_nothing_else(client, both):
    toggle(client, both, "foil")
    row = page_row(client, both)
    for finish in ("nonfoil", "foil"):
        form = next(p for p in row.split("<form")[1:] if f'value="{finish}"' in p)
        assert form.count("<button") == 1 and "×" not in form.split("</form>")[0]


def test_a_box_never_adds_a_second_copy(client, catalog, both):
    toggle(client, both, "foil")
    answer = toggle(client, both, "foil").text  # a second tab, or no JavaScript
    assert box(answer) == (True, False, "remove")
    assert entries(catalog) == [(both["id"], 1, "foil", "NM", "en", None)]


def test_copies_added_elsewhere_are_taken_back_one_at_a_time(client, catalog, both):
    with closing(db.connect(catalog)) as conn:
        service.add_entry(conn, both["id"], 2, finish="foil")
    answer = toggle(client, both, "foil", "remove").text
    assert box(answer) == (True, False, "remove") and "×1</span>" in answer
    assert entries(catalog)[0][1] == 1
    toggle(client, both, "foil")  # still ticked: nothing is added
    assert entries(catalog)[0][1] == 1
    answer = toggle(client, both, "foil", "remove").text
    assert box(answer) == (False, False, "add") and entries(catalog) == []


def test_the_page_shows_what_was_ticked(client, both):
    toggle(client, both, "foil")
    row = page_row(client, both)
    assert box(row, "foil") == (True, False, "remove")
    assert box(row, "nonfoil") == (False, False, "add")


def test_without_javascript_the_page_is_reloaded(client, catalog, both):
    here = f"/search?q=set%3A{both['set']}&view=input"
    data = {"printing": both["id"], "finish": "nonfoil", "action": "add", "next": here}
    answer = client.post("/fast/toggle", data=data)
    assert answer.status_code == 303 and answer.headers["location"] == here
    assert len(entries(catalog)) == 1
    elsewhere = client.post("/fast/toggle", data={**data, "next": "https://evil.example/"})
    assert elsewhere.headers["location"] == "/fast"


def test_the_row_keeps_the_way_back(client, both):
    here = f"/search?q=set%3A{both['set']}&view=input"
    row = next(r for r in client.get(here).text.split("<tr>") if both["id"] in r)
    assert row.count(f'name="next" value="/search?q=set%3A{both["set"]}&amp;view=input"') == 2


# --- where new copies go --------------------------------------------------------------


def test_copies_get_the_remembered_place_condition_and_language(client, catalog, both):
    client.post("/locations", data={"name": "New set", "kind": "binder"})
    here = f"/search?q=set%3A{both['set']}&view=input"
    answer = client.get(
        "/fast/settings",
        params={"location": "1", "condition": "EX", "language": "it", "next": here},
    )
    assert answer.status_code == 303 and answer.headers["location"] == here

    toggle(client, both)
    assert entries(catalog) == [(both["id"], 1, "nonfoil", "EX", "it", "New set")]
    page = client.get(here).text
    assert '<option value="1" selected>New set</option>' in page
    assert '<option value="EX" selected>' in page and '<option value="it" selected>' in page

    # the boxes are about these copies only: elsewhere the same card is not ticked
    client.get("/fast/settings", params={"location": "", "condition": "EX", "language": "it"})
    row = next(r for r in client.get(here).text.split("<tr>") if both["id"] in r)
    assert box(row, "nonfoil") == (False, False, "add") and "×1</span>" in row
    toggle(client, both)
    assert [e[5] for e in entries(catalog)] == ["New set", None]


def test_settings_ignore_nonsense_and_share_quick_adds_memory(client, catalog, both):
    client.post("/add", data={"printing": both["id"], "condition": "GD", "language": "de"})
    client.get("/fast/settings", params={"condition": "??", "language": "xx", "location": "x"})
    toggle(client, both, "foil")
    assert entries(catalog)[-1] == (both["id"], 1, "foil", "GD", "de", None)
    assert client.get("/fast/settings").headers["location"] == "/fast"
    bad = client.get("/fast/settings", params={"next": "//evil.example"})
    assert bad.headers["location"] == "/fast"


def test_a_location_that_was_deleted_becomes_no_location(client, catalog, both):
    client.post("/locations", data={"name": "Gone", "kind": "box"})
    client.get("/fast/settings", params={"location": "1"})
    client.post("/locations/1/delete")
    toggle(client, both)
    assert entries(catalog) == [(both["id"], 1, "nonfoil", "NM", "en", None)]


# --- refusals -------------------------------------------------------------------------


def test_nonsense_is_refused(client, catalog, both):
    assert toggle(client, {"id": UNKNOWN}).status_code == 404
    assert toggle(client, {"id": "nonsense"}).status_code == 404
    assert toggle(client, both, "etched").status_code == 404
    assert toggle(client, both, action="explode").status_code == 404
    assert client.get("/fast/toggle").status_code in (404, 405)  # a change is a POST
    cross = client.post(
        "/fast/toggle",
        data={"printing": both["id"]},
        headers={"Origin": "https://evil.example"},
    )
    assert cross.status_code == 403 and entries(catalog) == []


def test_locked_collection_shows_the_boxes_but_does_not_change(client, catalog, both):
    toggle(client, both)
    client.post("/lock", data={"state": "on"})
    assert toggle(client, both, "foil").status_code == 423
    assert len(entries(catalog)) == 1
    page = client.get(f"/search?q=set:{both['set']}&view=input").text
    assert "locked-notice" in page
    row = next(r for r in page.split("<tr>") if both["id"] in r)
    assert box(row, "nonfoil") == (True, True, "remove")
    assert box(row, "foil") == (False, True, "add")


def test_copies_like_and_take_one(catalog, both):
    key = {"condition": "NM", "language": "en", "location_id": None}
    with closing(db.connect(catalog)) as conn:
        assert service.copies_like(conn, [], **key) == {}
        service.add_entry(conn, both["id"], 3, finish="foil")
        service.add_entry(conn, both["id"], 1, finish="foil", condition="EX")
        assert service.copies_like(conn, [both["id"], UNKNOWN], **key) == {(both["id"], "foil"): 3}
        assert service.take_one(conn, both["id"], finish="foil", **key) is True
        assert service.take_one(conn, both["id"], finish="nonfoil", **key) is False
        assert service.copies_like(conn, [both["id"]], **key) == {(both["id"], "foil"): 2}


def test_in_italian(client, both):
    client.get("/lang/it")
    page = client.get(f"/search?q=set:{both['set']}&view=input").text
    assert ">Inserimento rapido</a>" in page and "Le nuove copie vanno in" in page
    assert ">Normale</th>" in page
