"""Search queries against the fixture catalog (SPEC §6.2, §6.3).

Expected results are computed here straight from the fixture JSON, independently of the
catalog columns, so these tests check ingest and search against each other.
"""

from contextlib import closing

import pytest

from magicdb import db, search
from magicdb.catalog.ingest import is_extra, normalize
from magicdb.search import QueryError
from magicdb.search.compiler import BY_KEY, FILTERS

# --- reading the fixture the "slow, obvious" way ---------------------------------------


def faces(c):
    return c.get("card_faces") or []


def col(c):
    if c.get("colors") is not None:
        return set(c["colors"])
    return {letter for face in faces(c) for letter in face.get("colors") or ()}


def ident(c):
    return set(c["color_identity"])


def otext(c):
    if c.get("oracle_text") is not None:
        return c["oracle_text"].lower()
    return "\n//\n".join(face.get("oracle_text") or "" for face in faces(c)).lower()


def tline(c):
    return c["type_line"].lower()


def kws(c):
    return [k.lower() for k in c.get("keywords") or ()]


def cost(c):
    if c.get("mana_cost") is not None:
        return c["mana_cost"]
    return " // ".join(face["mana_cost"] for face in faces(c) if face.get("mana_cost"))


def first(c, key):
    if c.get(key) is not None:
        return c[key]
    return faces(c)[0].get(key) if faces(c) else None


def num(c, key):
    try:
        return float(first(c, key))
    except (TypeError, ValueError):
        return None


def legal(c, fmt):
    return c["legalities"].get(fmt) in ("legal", "restricted")


def eur(c):
    value = c["prices"].get("eur")
    return float(value) if value is not None else None


def usd(c):
    value = c["prices"].get("usd")
    return float(value) if value is not None else None


def cn_sort(c):
    digits = ""
    for ch in c["collector_number"]:
        if not ch.isdigit():
            break
        digits += ch
    return int(digits) if digits else 0


def nm(c):
    return normalize(c["name"])


def has_back(c):
    return "image_uris" not in c and len(faces(c)) > 1 and "image_uris" in faces(c)[1]


RANK = {"common": 0, "uncommon": 1, "rare": 2, "special": 3, "mythic": 4, "bonus": 5}
W, U, B, R, G = "W", "U", "B", "R", "G"

# (query, predicate over a fixture card, does the query show extras?)
CASES = [
    # names
    ("bolt", lambda c: "bolt" in nm(c)),
    ("lightning bolt", lambda c: "lightning" in nm(c) and "bolt" in nm(c)),
    ("BOLT", lambda c: "bolt" in nm(c)),
    ('"serra angel"', lambda c: "serra angel" in nm(c)),
    ('!"Lightning Bolt"', lambda c: c["name"] == "Lightning Bolt"),
    ("!bolt", lambda c: False),
    ('!"Delver of Secrets"', lambda c: c["name"].startswith("Delver of Secrets //")),
    ('!"Insectile Aberration"', lambda c: c["name"].endswith("// Insectile Aberration")),
    ("name:angel", lambda c: "angel" in nm(c)),
    ('name="Serra Angel"', lambda c: c["name"] == "Serra Angel"),
    ("-angel", lambda c: "angel" not in nm(c)),
    # text, type, keyword
    ("o:flying", lambda c: "flying" in otext(c)),
    ('o:"draw a card"', lambda c: "draw a card" in otext(c)),
    ("oracle:trample", lambda c: "trample" in otext(c)),
    ("o:flying o:vigilance", lambda c: "flying" in otext(c) and "vigilance" in otext(c)),
    ("t:dragon", lambda c: "dragon" in tline(c)),
    ("t:legendary t:creature", lambda c: "legendary" in tline(c) and "creature" in tline(c)),
    ("type:instant", lambda c: "instant" in tline(c)),
    ('t:"human wizard"', lambda c: "human wizard" in tline(c)),
    ("-t:creature", lambda c: "creature" not in tline(c)),
    ("kw:flying", lambda c: "flying" in kws(c)),
    ('kw:"first strike"', lambda c: "first strike" in kws(c)),
    ("keyword:lifelink", lambda c: "lifelink" in kws(c)),
    ("kw:fly", lambda c: False),  # a keyword is matched whole
    # colours
    ("c:r", lambda c: {R} <= col(c)),
    ("c:rw", lambda c: {R, W} <= col(c)),
    ("c>=u", lambda c: {U} <= col(c)),
    ("c=r", lambda c: col(c) == {R}),
    ("c<=rg", lambda c: col(c) <= {R, G}),
    ("c>r", lambda c: {R} < col(c)),
    ("c<wu", lambda c: col(c) < {W, U}),
    ("c!=r", lambda c: col(c) != {R}),
    ("c:c", lambda c: not col(c)),
    ("c:colorless", lambda c: not col(c)),
    ("c!=c", lambda c: bool(col(c))),
    ("c:m", lambda c: len(col(c)) >= 2),
    ("c!=m", lambda c: len(col(c)) < 2),
    ("c:red", lambda c: {R} <= col(c)),
    ("color:blue", lambda c: {U} <= col(c)),
    ("c:wubrg", lambda c: len(col(c)) == 5),
    ("c=WU", lambda c: col(c) == {W, U}),
    ("id:wr", lambda c: ident(c) <= {W, R}),
    ("id<=wr", lambda c: ident(c) <= {W, R}),
    ("id=wr", lambda c: ident(c) == {W, R}),
    ("id>=wr", lambda c: {W, R} <= ident(c)),
    ("identity:c", lambda c: not ident(c)),
    ("id:m", lambda c: len(ident(c)) >= 2),
    ("id:g t:land", lambda c: ident(c) <= {G} and "land" in tline(c)),
    # numbers
    ("mv=3", lambda c: c.get("cmc") == 3),
    ("mv:0", lambda c: c.get("cmc") == 0),
    ("mv<=1", lambda c: c.get("cmc", 0) <= 1),
    ("cmc>7", lambda c: c.get("cmc", 0) > 7),
    ("mv!=2", lambda c: c.get("cmc") != 2),
    ("mv>=15", lambda c: c.get("cmc", 0) >= 15),
    ("mv>=2 mv<4", lambda c: 2 <= c.get("cmc", 0) < 4),
    ("m:{R}{R}", lambda c: "{R}{R}" in cost(c)),
    ("m:rr", lambda c: "{R}{R}" in cost(c)),
    ("mana:2rr", lambda c: "{2}{R}{R}" in cost(c)),
    ("m:{R/W}", lambda c: "{R/W}" in cost(c)),
    ("m:x", lambda c: "{X}" in cost(c)),
    ("pow>=6", lambda c: num(c, "power") is not None and num(c, "power") >= 6),
    ("pow<1", lambda c: num(c, "power") is not None and num(c, "power") < 1),
    ("pow=4 tou=4", lambda c: num(c, "power") == 4 and num(c, "toughness") == 4),
    ("tou>=7", lambda c: num(c, "toughness") is not None and num(c, "toughness") >= 7),
    ("power:2", lambda c: num(c, "power") == 2),
    ("-pow>=1", lambda c: not (num(c, "power") is not None and num(c, "power") >= 1)),
    ("loy=3", lambda c: num(c, "loyalty") == 3),
    ("loy>4", lambda c: num(c, "loyalty") is not None and num(c, "loyalty") > 4),
    # rarity, set, number
    ("r:mythic", lambda c: c["rarity"] == "mythic"),
    ("r:c", lambda c: c["rarity"] == "common"),
    ("rarity:uncommon", lambda c: c["rarity"] == "uncommon"),
    ("r>=rare", lambda c: RANK[c["rarity"]] >= 2),
    ("r<u", lambda c: RANK[c["rarity"]] < 1),
    ("r!=c", lambda c: c["rarity"] != "common"),
    ("set:arn", lambda c: c["set"] == "arn", True),
    ("s:dmu", lambda c: c["set"] == "dmu", True),
    ("e:LEA", lambda c: c["set"] == "lea", True),
    ("set:tdom", lambda c: c["set"] == "tdom", True),
    ("set!=arn", lambda c: c["set"] != "arn"),
    ("-set:arn", lambda c: c["set"] != "arn"),
    ("set:lea cn:161", lambda c: c["set"] == "lea" and c["collector_number"] == "161", True),
    ("set:arn cn<=10", lambda c: c["set"] == "arn" and cn_sort(c) <= 10, True),
    ("st:expansion", lambda c: c["set_type"] == "expansion"),
    ("st:core", lambda c: c["set_type"] == "core"),
    # formats, artist, flavour, dates, prices
    ("f:modern", lambda c: legal(c, "modern")),
    ("f:standard", lambda c: legal(c, "standard")),
    ("legal:vintage", lambda c: legal(c, "vintage")),
    ("-f:legacy", lambda c: not legal(c, "legacy")),
    ("a:guay", lambda c: "guay" in (c.get("artist") or "").lower()),
    ('artist:"christopher rush"', lambda c: "christopher rush" in (c.get("artist") or "").lower()),
    ("ft:the", lambda c: "the" in (first(c, "flavor_text") or "").lower()),
    ("year=1993", lambda c: c["released_at"][:4] == "1993"),
    ("year>=2020", lambda c: c["released_at"][:4] >= "2020"),
    ("year<2000", lambda c: c["released_at"][:4] < "2000"),
    ("date>=2020-01-01", lambda c: c["released_at"] >= "2020-01-01"),
    ("date<1994-01-01", lambda c: c["released_at"] < "1994-01-01"),
    ("eur>=100", lambda c: eur(c) is not None and eur(c) >= 100),
    ("eur<1", lambda c: eur(c) is not None and eur(c) < 1),
    ("usd>50", lambda c: usd(c) is not None and usd(c) > 50),
    ("-eur>=0", lambda c: eur(c) is None),
    # is:
    ("is:foil", lambda c: "foil" in c["finishes"]),
    ("is:nonfoil", lambda c: "nonfoil" in c["finishes"]),
    ("is:etched", lambda c: "etched" in c["finishes"]),
    ("is:reprint", lambda c: c["reprint"]),
    ("-is:reprint", lambda c: not c["reprint"]),
    ("is:promo", lambda c: c["promo"]),
    ("is:digital", lambda c: c["digital"]),
    ("is:dfc", has_back),
    ("is:extra", is_extra, True),
    ("-is:extra", lambda c: True),
    # combinations
    ("t:dragon or t:angel", lambda c: "dragon" in tline(c) or "angel" in tline(c)),
    (
        "(t:dragon or t:angel) c:w",
        lambda c: ("dragon" in tline(c) or "angel" in tline(c)) and {W} <= col(c),
    ),
    (
        "t:dragon or t:angel c:w",
        lambda c: "dragon" in tline(c) or ("angel" in tline(c) and {W} <= col(c)),
    ),
    ("-(t:creature or t:land)", lambda c: "creature" not in tline(c) and "land" not in tline(c)),
    ("-t:creature -t:land", lambda c: "creature" not in tline(c) and "land" not in tline(c)),
    (
        "c:r -t:creature mv<=2",
        lambda c: {R} <= col(c) and "creature" not in tline(c) and c.get("cmc", 0) <= 2,
    ),
    ("--t:dragon", lambda c: "dragon" in tline(c)),
    ("t:creature and c:g", lambda c: "creature" in tline(c) and {G} <= col(c)),
    # extras
    ("", lambda c: True),
    ("include:extras", lambda c: True, True),
    ("t:token", lambda c: "token" in tline(c)),
    ("t:token include:extras", lambda c: "token" in tline(c), True),
]


@pytest.fixture
def conn(catalog):
    with closing(db.connect(catalog)) as connection:
        yield connection


def run(conn, query, **kwargs):
    return search.run(conn, query, per_page=1000, **kwargs)


def test_there_are_enough_cases():
    assert len(CASES) >= 60


@pytest.mark.parametrize("case", CASES, ids=lambda case: case[0] or "(empty)")
def test_query(conn, fixture_cards, case):
    query, predicate, *extras = case
    shown = [c for c in fixture_cards if extras or not is_extra(c)]
    expected = {c["id"] for c in shown if predicate(c)}

    prints = run(conn, f"{query} unique:prints")
    assert {row["id"] for row in prints.rows} == expected
    assert prints.total == len(expected)

    # one row per card, and it is one of the matching printings
    cards = run(conn, query)
    by_id = {c["id"]: c for c in fixture_cards}
    assert {row["id"] for row in cards.rows} <= expected
    assert sorted(row["oracle_id"] for row in cards.rows) == sorted(
        {by_id[i]["oracle_id"] for i in expected}
    )


def test_cases_are_not_vacuous(conn, fixture_cards):
    """Guard against a fixture change silently emptying these tests."""
    empty = [q for q, predicate, *_ in CASES if not any(predicate(c) for c in fixture_cards)]
    # "is:promo" is empty only because the fixture has no promo printing
    assert sorted(empty) == ["!bolt", "is:promo", "kw:fly"]


def test_every_filter_key_in_the_spec_exists():
    keys = (
        "name o oracle t type kw keyword c color id identity mv cmc m mana pow tou loy r rarity "
        "s e set cn st f legal a artist ft year date eur usd is have haveprint qty loc cond "
        "finish lang unique order dir include"
    )
    assert set(keys.split()) <= set(BY_KEY)
    assert sum(len(d.keys) for d in FILTERS) == len(BY_KEY)  # no key defined twice


def test_every_documented_example_runs(conn):
    for definition in FILTERS:
        assert definition.summary and definition.example
        for example in definition.example.split("  "):
            run(conn, example)


# --- result modes, ordering, paging ---------------------------------------------------


def test_unique_cards_shows_the_preferred_printing(conn):
    result = run(conn, '!"Lightning Bolt"')
    assert result.unique == "cards" and result.total == 1
    assert result.rows[0]["set_code"] == "2x2"
    result = run(conn, '!"Lightning Bolt" unique:prints')
    assert result.unique == "prints" and result.total == 6
    # a print-level filter picks among the printings that match
    assert run(conn, '!"Lightning Bolt" year<2000').rows[0]["set_code"] == "4ed"
    assert run(conn, '!"Lightning Bolt" set:lea').rows[0]["set_code"] == "lea"


def test_directives_apply_to_the_whole_search_wherever_they_are(conn):
    plain = run(conn, "t:dragon or t:angel")
    for query in [
        "t:dragon or t:angel unique:prints order:mv",
        "unique:prints order:mv t:dragon or t:angel",
        "(t:dragon or t:angel) (unique:prints order:mv)",
        "t:dragon order:mv or unique:prints t:angel",
    ]:
        result = run(conn, query)
        assert (result.unique, result.order) == ("prints", "mv"), query
        assert {row["oracle_id"] for row in result.rows} == {row["oracle_id"] for row in plain.rows}
    assert run(conn, "unique:prints or order:mv").total == run(conn, "unique:prints").total


def test_totals(conn, fixture_cards):
    shown = [c for c in fixture_cards if not is_extra(c)]
    assert run(conn, "unique:prints").total == len(shown)
    assert run(conn, "").total == len({c["oracle_id"] for c in shown})


def keys(result, column):
    return [row[column] for row in result.rows]


def test_default_order_is_by_name(conn):
    result = run(conn, "")
    assert (result.order, result.direction) == ("name", "asc")
    assert keys(result, "name_norm") == sorted(keys(result, "name_norm"))
    result = run(conn, "dir:desc")
    assert keys(result, "name_norm") == sorted(keys(result, "name_norm"), reverse=True)


def test_orders(conn):
    assert keys(run(conn, "order:mv"), "cmc") == sorted(keys(run(conn, ""), "cmc"))
    assert keys(run(conn, "order:cmc dir:desc"), "cmc")[0] == 15

    released = run(conn, "order:released unique:prints")
    assert released.direction == "desc"  # newest first unless told otherwise
    assert keys(released, "released_at") == sorted(keys(released, "released_at"), reverse=True)
    oldest = run(conn, "order:released dir:asc unique:prints")
    assert oldest.rows[0]["released_at"] == min(keys(released, "released_at"))

    rarity = run(conn, "order:rarity")
    assert keys(rarity, "rarity_ord") == sorted(keys(rarity, "rarity_ord"), reverse=True)

    by_set = run(conn, "order:set unique:prints set:arn")
    assert keys(by_set, "collector_sort") == sorted(keys(by_set, "collector_sort"))


def test_order_by_price_puts_unpriced_last(conn):
    for query, reverse in [("order:price", True), ("order:price dir:asc", False)]:
        result = run(conn, f"{query} unique:prints")
        prices = keys(result, "price_eur")
        priced = [p for p in prices if p is not None]
        assert priced and len(priced) < len(prices)
        assert prices == sorted(priced, reverse=reverse) + [None] * (len(prices) - len(priced))
    dollars = run(conn, "order:price unique:prints", currency="usd")
    priced = [p for p in keys(dollars, "price_usd") if p is not None]
    assert priced == sorted(priced, reverse=True)


def test_order_is_stable_between_runs(conn):
    assert keys(run(conn, "order:mv"), "id") == keys(run(conn, "order:mv"), "id")


def test_paging(conn):
    everything = run(conn, "unique:prints")
    first = search.run(conn, "unique:prints", 1, per_page=100)
    second = search.run(conn, "unique:prints", 2, per_page=100)
    third = search.run(conn, "unique:prints", 3, per_page=100)
    assert first.total == second.total == third.total == everything.total
    assert first.pages == 3 and (first.page, second.page) == (1, 2)
    assert keys(first, "id") + keys(second, "id") + keys(third, "id") == keys(everything, "id")
    assert len(third.rows) == everything.total - 200

    beyond = search.run(conn, "unique:prints", 9, per_page=100)
    assert beyond.rows == [] and beyond.total == everything.total and beyond.pages == 3
    assert search.run(conn, "", 0).page == 1
    assert search.run(conn, "", -5).page == 1
    assert search.run(conn, "!nothing").pages == 1
    assert len(search.run(conn, "").rows) == search.PER_PAGE


# --- collection filters ---------------------------------------------------------------


def own(conn, name, set_code, quantity=1, finish="nonfoil", condition="NM", lang="en", loc=None):
    card = conn.execute(
        "SELECT * FROM cat.printings WHERE name = ? AND set_code = ?", (name, set_code)
    ).fetchone()
    location_id = None
    if loc:
        conn.execute("INSERT OR IGNORE INTO locations (name, created_at) VALUES (?, 'now')", (loc,))
        location_id = conn.execute("SELECT id FROM locations WHERE name = ?", (loc,)).fetchone()[0]
    conn.execute(
        "INSERT INTO entries (scryfall_id, oracle_id, name, set_code, collector_number, quantity,"
        " finish, condition, language, location_id, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'now', 'now')",
        (
            card["id"],
            card["oracle_id"],
            name,
            set_code,
            card["collector_number"],
            quantity,
            finish,
            condition,
            lang,
            location_id,
        ),
    )


@pytest.fixture
def owned(conn):
    own(conn, "Lightning Bolt", "m10", 3, loc="Binder 1")
    own(conn, "Lightning Bolt", "m10", 2, finish="foil", condition="EX", loc="Blue box")
    own(conn, "Lightning Bolt", "lea", 1, condition="PL", loc="Blue box")
    own(conn, "Serra Angel", "dom", 4, lang="it", loc="Deck 100%")
    own(conn, "Sol Ring", "c21", 1)
    return conn


def found(conn, query):
    return sorted((row["name"], row["set_code"]) for row in run(conn, query).rows)


BOLT_M10, BOLT_LEA = ("Lightning Bolt", "m10"), ("Lightning Bolt", "lea")
ANGEL, RING = ("Serra Angel", "dom"), ("Sol Ring", "c21")


def test_have(owned):
    assert {name for name, _ in found(owned, "have:yes")} == {
        "Lightning Bolt",
        "Serra Angel",
        "Sol Ring",
    }
    assert len(found(owned, "have:yes unique:prints")) == 8  # all six Bolts count
    assert run(owned, "have:no").total == run(owned, "").total - 3
    assert found(owned, "have:yes t:angel") == [ANGEL]
    assert run(owned, "have:yes have:no").total == 0


def test_haveprint(owned):
    assert found(owned, "haveprint:yes unique:prints") == [BOLT_LEA, BOLT_M10, ANGEL, RING]
    others = found(owned, "have:yes haveprint:no unique:prints")
    assert len(others) == 4 and all(name == "Lightning Bolt" for name, _ in others)
    # in card mode the owned printing is shown, not the preferred one
    assert {s for _, s in found(owned, '!"Lightning Bolt" haveprint:yes')} <= {"m10", "lea"}


def test_qty(owned):
    assert found(owned, "qty>=4 unique:prints") == [BOLT_M10, ANGEL]  # 3 + 2 foil = 5
    assert found(owned, "qty=5 unique:prints") == [BOLT_M10]
    assert found(owned, "qty:1 unique:prints") == [BOLT_LEA, RING]
    assert run(owned, "qty=0 unique:prints").total == run(owned, "unique:prints").total - 4
    assert found(owned, "qty>0 qty<4 unique:prints") == [BOLT_LEA, RING]


def test_loc(owned):
    assert found(owned, "loc:binder unique:prints") == [BOLT_M10]
    assert found(owned, 'loc:"blue box" unique:prints') == [BOLT_LEA, BOLT_M10]
    assert found(owned, "loc:BLUE unique:prints") == [BOLT_LEA, BOLT_M10]
    assert found(owned, "loc:garage unique:prints") == []
    assert found(owned, 'loc:"100%" unique:prints') == [ANGEL]
    assert found(owned, "loc:% unique:prints") == [ANGEL]  # % is a character, not a wildcard
    assert found(owned, "haveprint:yes -loc:b unique:prints") == [ANGEL, RING]


def test_cond_finish_lang(owned):
    assert found(owned, "cond:ex unique:prints") == [BOLT_M10]
    assert found(owned, "cond:PL unique:prints") == [BOLT_LEA]
    assert found(owned, "cond:nm unique:prints") == [BOLT_M10, ANGEL, RING]
    assert found(owned, "cond:mt unique:prints") == []
    assert found(owned, "finish:foil unique:prints") == [BOLT_M10]
    assert found(owned, "finish:etched unique:prints") == []
    assert found(owned, "lang:it unique:prints") == [ANGEL]
    assert found(owned, "lang:EN t:artifact unique:prints") == [RING]


def test_collection_filters_on_an_empty_collection(conn):
    assert run(conn, "have:yes").total == 0
    assert run(conn, "have:no").total == run(conn, "").total
    assert run(conn, "qty>=1 or loc:x or cond:nm or finish:foil or lang:en").total == 0


# --- errors ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("foo:bar", "'foo' is not something you can search by"),
        ("mv:abc", "'mv' needs a number, not 'abc'"),
        ("cmc>x", "'mv' needs a number"),
        ("pow:big", "'pow' needs a number"),
        ("eur:cheap", "'eur' needs a number"),
        ("qty:many", "'qty' needs a number"),
        ("year:mm", "'year' needs a number"),
        ("mv:nan", "needs a number"),
        ("mv:1e999", "needs a number"),
        ("set:arn cn>abc", "'cn' needs a number"),
        ("c:xyz", "'c:' accepts the letters"),
        ("id:purple", "'id:' accepts the letters"),
        ("r:legendary", "'r:' accepts"),
        ("is:shiny", "'is:' accepts foil"),
        ("have:maybe", "'have:' accepts yes or no"),
        ("haveprint:2", "'haveprint:' accepts yes or no"),
        ("date:2024", "needs a date like"),
        ("date>=01/02/2024", "needs a date like"),
        ("cond:mint", "'cond:' accepts MT, NM"),
        ("finish:shiny", "'finish:' accepts nonfoil"),
        ("m:2r!", "'m:' accepts"),
        ("t>dragon", "'t' cannot be used with '>'"),
        ("o!=x", "'o' cannot be used with '!='"),
        ("is=foil", "'is' cannot be used with '='"),
        ("have>yes", "'have' cannot be used with '>'"),
        ("order:foo", "'order:' accepts name"),
        ("unique:x", "'unique:' accepts cards, prints"),
        ("dir:up", "'dir:' accepts asc, desc"),
        ("include:tokens", "'include:' accepts extras"),
        ("order>name", "'order' cannot be used with '>'"),
        ("-order:name", "cannot be used after '-'"),
        ("-(bolt unique:prints)", "cannot be used after '-'"),
        ("order:name order:mv", "given more than once"),
        ('"unclosed', "never closed"),
        ("(t:dragon", "never closed"),
        ("t:dragon)", "without a matching"),
    ],
)
def test_bad_queries_explain_themselves(conn, query, message):
    with pytest.raises(QueryError, match=message):
        run(conn, query)


def test_without_a_catalog(settings):
    db.migrate(settings)
    with closing(db.connect(settings)) as conn, pytest.raises(search.CatalogUnavailable):
        search.run(conn, "bolt")


# --- hostile input --------------------------------------------------------------------

HOSTILE = [
    "'; DROP TABLE printings; --",
    "' OR '1'='1",
    '" OR 1=1 --',
    'o:"\'; DROP TABLE entries; --"',
    "name:'); DELETE FROM entries; --",
    'loc:"x\' OR 1=1 --"',
    "t:x' UNION SELECT * FROM sqlite_master --",
    "mv:1;DROP TABLE entries",
    "c:w;--",
    "set:arn' --",
    "%",
    "_",
    "o:%",
    "name:_",
    "\\",
    "o:\\",
    'o:"\\%"',
    "\x00",
    "o:\x00",
    "ünïcödé 日本語 🔥",
    "t:" + "x" * 1900,
    "c:" + "w" * 1000,
    "qty>=99999999999999999999",
    "cn>" + "9" * 400,
    "((((",
    "))))",
    "-" * 3000,
    "(" * 5000,
    "a " * 5000,  # 10 kB
    "t:a or " * 1500,
    '"' * 999,
    "!" * 500,
    ": = != < > <= >=",
    "?",
    "? ?1 :name @x $y",
]


@pytest.mark.parametrize("text", HOSTILE, ids=range(len(HOSTILE)))
def test_hostile_input_gives_results_or_a_query_error(owned, text):
    before = owned.execute("SELECT COUNT(*) FROM cat.printings").fetchone()[0]
    for mode in ("", " unique:prints"):
        try:
            result = run(owned, text + mode)
        except QueryError as error:
            assert str(error)
        else:
            assert 0 <= result.total <= before
    assert owned.execute("SELECT COUNT(*) FROM cat.printings").fetchone()[0] == before
    assert owned.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 5
    assert owned.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 3


def test_wildcard_characters_are_literal(conn):
    everything = run(conn, "unique:prints").total
    assert run(conn, "name:_ unique:prints").total == 0
    assert run(conn, "% unique:prints").total == 0
    assert 0 < run(conn, "o:a unique:prints").total < everything


# --- did you mean ---------------------------------------------------------------------


def test_suggestions_for_a_misspelt_name(conn):
    result = run(conn, "lightnig bolt")
    assert result.total == 0 and result.suggestions[0] == "Lightning Bolt"
    assert "Serra Angel" in run(conn, "sera angel").suggestions
    assert "Jace, the Mind Sculptor" in run(conn, "jace mind sculpter").suggestions
    assert len(run(conn, "dragn").suggestions) <= search.SUGGESTIONS


def test_no_suggestions_when_they_would_not_help(conn):
    assert run(conn, "qqqqzzzzxxxx").suggestions == []
    assert run(conn, "bolt").suggestions == []  # there are results
    assert run(conn, "lightnig t:instant").suggestions == []  # not a plain name search
    assert run(conn, '!"lightnig bolt"').suggestions == []
    assert run(conn, "t:nothing").suggestions == []
