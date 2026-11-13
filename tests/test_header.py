"""The header: suggestions, where to search, the menu; and sortable columns (SPEC §7)."""

from contextlib import closing
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from magicdb import db, search, web
from magicdb.app import create_app
from magicdb.collection import service as collection
from magicdb.templating import search_url


@pytest.fixture
def client(catalog):
    return TestClient(create_app(catalog), follow_redirects=False)


@pytest.fixture
def ids(fixture_cards):
    return lambda name, set_code: next(
        c["id"] for c in fixture_cards if c["name"] == name and c["set"] == set_code
    )


@pytest.fixture
def owned(client, ids):
    """Owned: 2 Lightning Bolt (Alpha), 1 Serra Angel, 3 Sol Ring."""
    for name, set_code, copies in [
        ("Lightning Bolt", "lea", 2),
        ("Serra Angel", "dom", 1),
        ("Sol Ring", "c21", 3),
    ]:
        client.post("/add", data={"printing": ids(name, set_code), "quantity": str(copies)})
    return client


def header(page: str) -> str:
    return page.split('<header class="site-header">')[1].split("</header>")[0]


def links(fragment: str) -> list[str]:
    return [part.split('"')[0] for part in fragment.split('href="')[1:]]


# --- 3, 4, 5: the menu ----------------------------------------------------------------


def test_menu_has_only_collection_and_decks(client):
    top = header(client.get("/").text)
    menu = top.split('class="site-nav"')[1].split("</nav>")[0]
    assert links(menu) == ["/stats", "/decks"]
    assert ">My collection</a>" in menu and ">My decks</a>" in menu
    assert 'href="/add"' not in top and "Filters" not in top
    assert '<a class="brand" href="/" title=' in top  # the logo is the way to the filters


def test_my_collection_opens_the_statistics_with_the_ways_onward(owned):
    page = owned.get("/stats").text
    assert '<h1 class="page-title">My collection</h1>' in page and "Statistics" in page
    hub = page.split('<p class="links">')[1].split("</p>")[0]
    assert links(hub) == ["/collection", "/add", "/fast", "/locations", "/import", "/export"]
    assert "<dd>6</dd>" in page  # and the numbers are there


def test_add_cards_is_reachable_from_administration_and_collection(client):
    assert 'href="/add"' in client.get("/admin").text
    assert 'href="/add"' in client.get("/collection").text
    assert client.get("/add").status_code == 200


# --- 2: where to search ---------------------------------------------------------------


def test_search_button_offers_both_scopes(client):
    top = header(client.get("/search?q=bolt").text)
    assert '<button type="submit" name="scope" value="all">Search</button>' in top
    assert (
        '<button type="submit" name="scope" value="all" aria-current="true">Search all cards</button>'
        in top
    )
    assert (
        '<button type="submit" name="scope" value="collection">Search my collection</button>' in top
    )
    # the plain button comes first: it is the one Enter presses
    assert top.index('value="all">Search</button>') < top.index("split-menu")


def test_collection_scope_shows_only_what_is_owned(owned, ids):
    everything = owned.get(
        '/search?q=!"Lightning Bolt" or !"Serra Angel" or !"Sol Ring" or !"Shivan Dragon"'
    )
    mine = owned.get(
        '/search?q=!"Lightning Bolt" or !"Serra Angel" or !"Sol Ring" or !"Shivan Dragon"&scope=collection'
    )
    assert "\n    3 cards\n" in mine.text and "\n    3 cards\n" not in everything.text
    for name in ("Lightning Bolt", "Serra Angel", "Sol Ring"):
        assert name in mine.text
    # in the results, not in the search box that repeats the query
    found, found_all = (page.text.split('id="results"')[1] for page in (mine, everything))
    assert "Shivan Dragon" not in found and "Shivan Dragon" in found_all
    # the printing shown is the one in the collection, not the catalog's favourite
    assert f'href="/card/{ids("Lightning Bolt", "lea")}"' in mine.text
    assert f'href="/card/{ids("Lightning Bolt", "2x2")}"' in everything.text


def test_badges_show_in_both_scopes(owned):
    for scope in ("all", "collection"):
        page = owned.get(f'/search?q=!"Lightning Bolt"&scope={scope}').text
        assert '<span class="owned" title="You own 2">×2</span>' in page, scope


def test_scope_is_kept_by_the_button_the_views_and_the_sorting(owned):
    page = owned.get("/search?q=t:instant&scope=collection&view=list").text
    top = header(page)
    assert (
        '<button type="submit" name="scope" value="collection">Search my collection</button>' in top
    )
    assert 'value="collection" aria-current="true"' in top
    body = page.split('id="results"')[1]
    assert body.count("scope=collection") >= 8  # two view links and six column headers
    assert all("scope=collection" in link for link in links(body) if link.startswith("/search"))


def test_nothing_owned_matches(owned, client):
    page = owned.get("/search?q=t:dragon&scope=collection").text
    assert "No cards found in your collection." in page
    assert 'href="/search?q=t%3Adragon">Search all cards instead</a>' in page
    assert "No cards found." in owned.get("/search?q=t:nothing").text


@pytest.mark.parametrize("scope", ["", "everything", "COLLECTION", "collection' OR 1=1"])
def test_unknown_scope_means_all_cards(owned, scope):
    page = owned.get("/search", params={"q": "t:dragon", "scope": scope}).text
    assert "Shivan Dragon" in page and 'name="scope" value="all">Search</button>' in page


def test_owned_only_in_the_engine(catalog, owned, ids):
    with closing(db.connect(catalog)) as conn:
        prints = search.run(conn, "unique:prints", owned_only=True, per_page=500)
        assert {row["id"] for row in prints.rows} == {
            ids("Lightning Bolt", "lea"),
            ids("Serra Angel", "dom"),
            ids("Sol Ring", "c21"),
        }
        # "or" in the query cannot escape the scope
        either = search.run(conn, "t:dragon or t:angel", owned_only=True)
        assert [row["name"] for row in either.rows] == ["Serra Angel"]
        assert search.run(conn, "-t:angel", owned_only=True).total == 2
        assert search.run(conn, "lightnig bolt", owned_only=True).total == 0


def test_search_url_carries_the_scope():
    assert search_url("bolt") == "/search?q=bolt"
    assert (
        search_url("bolt", 2, "list", "collection")
        == "/search?q=bolt&page=2&view=list&scope=collection"
    )
    assert search_url("bolt", scope="all") == "/search?q=bolt"


# --- 1: suggestions while typing ------------------------------------------------------


def test_suggestions_are_links_to_the_exact_card(client):
    fragment = client.get("/search/suggest?q=light").text
    assert "<html" not in fragment and fragment.count('role="option"') >= 1
    assert (
        '<a role="option" href="/search?q=%21%22Lightning+Bolt%22"><b></b>Light<b>ning Bolt</b></a>'
        in fragment
    )
    target = urlsplit(links(fragment)[0])
    assert parse_qs(target.query)["q"][0].startswith('!"')
    assert client.get(links(fragment)[0]).status_code == 200


def test_suggestions_put_names_starting_with_it_first_and_stop_at_eight(client):
    fragment = client.get("/search/suggest?q=an").text
    names = [part.split("</a>")[0] for part in fragment.split('">')[1:]]
    assert 1 <= len(names) <= web.SUGGEST_LIMIT == 8
    starts = [name.startswith("<b></b>") for name in names]
    assert starts == sorted(starts, reverse=True)
    middle = client.get("/search/suggest?q=bolt").text
    assert "<b>Lightning </b>Bolt<b></b>" in middle  # what was typed stays plain


@pytest.mark.parametrize(
    "typed",
    [
        "",
        "l",
        " ",
        "t:dragon",
        "bolt c:r",
        "mv<=3",
        '"lightning',
        "!bolt",
        "(bolt)",
        "bolt or shock",
        "<script>alert(1)</script>",
        "zzzzzzzz",
    ],
)
def test_no_suggestions_for_syntax_or_nonsense(client, typed):
    assert client.get("/search/suggest", params={"q": typed}).text == ""


def test_suggestions_in_the_collection_are_only_owned_names(owned):
    fragment = owned.get("/search/suggest?q=li&scope=collection").text
    assert "<b></b>Li<b>ghtning Bolt</b>" in fragment
    assert fragment.count('role="option"') == 1  # Lightning Helix is not owned
    assert "scope=collection" in fragment
    assert owned.get("/search/suggest?q=shiv&scope=collection").text == ""
    assert "Shivan Dragon" in owned.get("/search/suggest?q=shiv").text.replace("<b>", "").replace(
        "</b>", ""
    )


def test_suggestion_names_are_escaped(catalog, client):
    with closing(db.connect(catalog)) as conn:
        collection.create_location(conn, "x")
        conn.execute(
            "INSERT INTO entries (scryfall_id, oracle_id, name, set_code, collector_number, quantity,"
            " created_at, updated_at) VALUES ('p', 'o', 'Evil <b>\"card\"</b> & co', 'x', '1', 1, 'n', 'n')"
        )
        conn.commit()
    fragment = client.get("/search/suggest?q=evil&scope=collection").text
    assert '<b>"card"' not in fragment and "&lt;b&gt;" in fragment and "&amp; co" in fragment
    assert fragment.count("<a ") == 1


def test_highlight():
    assert web.highlight("Lightning Bolt", "light") == "<b></b>Light<b>ning Bolt</b>"
    assert web.highlight("Lightning Bolt", "BOLT") == "<b>Lightning </b>Bolt<b></b>"
    assert web.highlight("Sol Ring", "xyz") == "<b>Sol Ring</b>"
    assert web.highlight("A & B", "&") == "<b>A </b>&amp;<b> B</b>"


def test_the_box_is_wired_for_suggestions(client):
    top = header(client.get("/search?q=bolt&scope=collection").text)
    assert 'hx-get="/search/suggest"' in top and 'id="suggestions"' in top
    assert 'hx-vals=\'{"scope": "collection"}\'' in top
    assert 'autocomplete="off"' in top
    page = client.get("/").text
    assert "/static/app.js" in page
    script = client.get("/static/app.js")
    assert script.status_code == 200 and "ArrowDown" in script.text


# --- 6: sorting by column -------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "order", "direction", "expected"),
    [
        ("t:dragon", "price", None, "t:dragon order:price"),
        ("t:dragon", "price", "asc", "t:dragon order:price dir:asc"),
        ("t:dragon order:mv dir:desc", "price", None, "t:dragon order:price"),
        ("order:mv t:dragon dir:asc c:r", "name", None, "t:dragon c:r"),
        ("t:dragon ORDER:Price", "name", "desc", "t:dragon order:name dir:desc"),
        ("", "set", None, "order:set"),
        ("order:price", "name", None, ""),
        ('o:"order:name stays" bolt', "mv", None, 'o:"order:name stays" bolt order:mv'),
        (
            "unique:prints include:extras",
            "rarity",
            "asc",
            "unique:prints include:extras order:rarity dir:asc",
        ),
    ],
)
def test_with_order(query, order, direction, expected):
    assert search.with_order(query, order, direction) == expected
    search.parser.parse(expected)  # and it is still a valid search


def test_sort_links_turn_the_current_order_round(catalog):
    with closing(db.connect(catalog)) as conn:
        by_name = search.run(conn, "t:dragon")
        assert search.sort_links("t:dragon", by_name) == {
            "name": "t:dragon order:name dir:desc",
            "mv": "t:dragon order:mv",
            "released": "t:dragon order:released",
            "rarity": "t:dragon order:rarity",
            "price": "t:dragon order:price",
            "set": "t:dragon order:set",
            "type": "t:dragon order:type",
        }
        by_price = search.run(conn, "t:dragon order:price")  # most expensive first
        assert (
            search.sort_links("t:dragon order:price", by_price)["price"]
            == "t:dragon order:price dir:asc"
        )
        turned = search.run(conn, "t:dragon order:price dir:asc")
        assert (
            search.sort_links("t:dragon order:price dir:asc", turned)["price"]
            == "t:dragon order:price dir:desc"
        )


def test_order_by_type(catalog):
    with closing(db.connect(catalog)) as conn:
        result = search.run(conn, "order:type", per_page=500)
        types = [row["type_norm"] for row in result.rows]
        assert (result.order, result.direction) == ("type", "asc") and types == sorted(types)
        reverse = search.run(conn, "order:type dir:desc", per_page=500)
        assert [row["type_norm"] for row in reverse.rows] == sorted(types, reverse=True)


def test_list_headers_sort_the_results(client):
    page = client.get("/search?q=t:dragon&view=list").text
    head = page.split("<thead>")[1].split("</thead>")[0]
    assert head.count('class="sorter') == 6
    assert (
        'class="sorter is-sorted" href="/search?q=t%3Adragon+order%3Aname+dir%3Adesc&amp;view=list"'
        in head
    )
    assert 'Name<span aria-hidden="true"> ▲</span>' in head
    assert 'href="/search?q=t%3Adragon+order%3Aprice&amp;view=list"' in head

    by_price = client.get("/search?q=t:dragon order:price&view=list").text
    head = by_price.split("<thead>")[1].split("</thead>")[0]
    assert 'Price<span aria-hidden="true"> ▼</span>' in head
    assert "order%3Aprice+dir%3Aasc" in head and "Name<span" not in head
    prices = [cell.split("<")[0] for cell in by_price.split('<td class="num">')[1:]]
    values = [float(p.strip("€").replace(",", "")) for p in prices if p != "—"]
    assert values == sorted(values, reverse=True) and len(values) >= 2
    assert 'value="t:dragon order:price"' in by_price  # the box shows the search as it is now


def test_grid_has_a_sort_line(client):
    page = client.get("/search?q=t:dragon").text
    line = page.split('<p class="sort-line">')[1].split("</p>")[0]
    assert line.count('class="sorter') == 7 and "Release date" in line and "is-sorted" in line
    assert "sort-line" not in client.get("/search?q=t:dragon&view=list").text


def test_collection_columns_sort_the_list(owned):
    page = owned.get("/collection?q=l&location=none").text
    head = page.split("<thead>")[1].split("</thead>")[0]
    for key in ("quantity", "name", "set", "location", "value", "added"):
        assert f'href="/collection?q=l&amp;location=none&amp;sort={key}"' in head, key
    assert (
        'class="sorter is-sorted" href="/collection?q=l&amp;location=none&amp;sort=added"' in head
    )
    by_quantity = owned.get("/collection?sort=quantity").text
    assert (
        by_quantity.index("Sol Ring")
        < by_quantity.index("Lightning Bolt")
        < by_quantity.index("Serra Angel")
    )
    assert 'class="sorter is-sorted" href="/collection?sort=quantity"' in by_quantity


def test_new_labels_in_italian(owned):
    owned.get("/lang/it")
    top = header(owned.get("/search?q=bolt&scope=collection").text)
    for expected in [
        "La mia collezione",
        "I miei mazzi",
        "Cerca in collezione",
        "Cerca tra tutte le carte",
    ]:
        assert expected in top, expected
    assert (
        "Nessuna carta trovata nella tua collezione."
        in owned.get("/search?q=t:dragon&scope=collection").text
    )
    assert "Elenco delle carte" in owned.get("/stats").text


# --- style sheets and scripts are refreshed when they change --------------------------


def test_static_files_carry_a_fingerprint_of_their_content(client):
    import hashlib
    import re

    from magicdb.templating import STATIC_DIR, asset

    page = client.get("/").text
    for name in ("app.css", "htmx.min.js", "app.js"):
        digest = hashlib.sha256((STATIC_DIR / name).read_bytes()).hexdigest()[:10]
        assert asset(name) == f"/static/{name}?v={digest}"
        assert asset(name) in page, name
        assert client.get(asset(name)).status_code == 200
    assert len(set(re.findall(r"\?v=([0-9a-f]{10})", page))) == 3  # one per file
