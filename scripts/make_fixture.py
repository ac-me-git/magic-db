"""Download the test fixture from Scryfall (SPEC §12).

Run once, by hand, when the fixture needs to change:

    python scripts/make_fixture.py

Writes tests/fixtures/cards.json (same shape as the Scryfall bulk file) and
tests/fixtures/sets.json. Tests never call the network; they only read these files.
"""

import json
import sys
import time
from pathlib import Path

import httpx

API = "https://api.scryfall.com"
HEADERS = {"User-Agent": "magic-db-fixture/0.1", "Accept": "application/json"}
OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

# (exact name, set code) — one printing each.
NAMED: list[tuple[str, str]] = [
    # Layout and edge cases required by SPEC §12
    ("Delver of Secrets", "isd"),  # transform DFC
    ("Valakut Awakening", "znr"),  # modal DFC
    ("Fire // Ice", "apc"),  # split
    ("Bonecrusher Giant", "eld"),  # adventure
    ("The Eldest Reborn", "dom"),  # saga
    ("Jace, the Mind Sculptor", "wwk"),  # planeswalker
    ("Invasion of Zendikar", "mom"),  # battle
    ("Tarmogoyf", "fut"),  # "*" power
    ("Sol Ring", "c21"),  # colourless artifact
    ("Niv-Mizzet Reborn", "war"),  # five colours
    ("Boros Reckoner", "gtc"),  # hybrid mana
    # One card, many printings
    ("Lightning Bolt", "lea"),
    ("Lightning Bolt", "4ed"),
    ("Lightning Bolt", "m10"),
    ("Lightning Bolt", "m11"),
    ("Lightning Bolt", "a25"),
    ("Lightning Bolt", "2x2"),
    # Variety for search tests
    ("Shivan Dragon", "m20"),
    ("Terror of the Peaks", "m21"),
    ("Serra Angel", "dom"),
    ("Baneslayer Angel", "m10"),
    ("Counterspell", "mh2"),
    ("Wrath of God", "10e"),
    ("Dark Ritual", "a25"),
    ("Giant Growth", "m14"),
    ("Swords to Plowshares", "a25"),
    ("Smuggler's Copter", "kld"),
    ("Fireball", "m12"),
    ("Gitaxian Probe", "nph"),
    ("Emrakul, the Aeons Torn", "roe"),
    ("Kozilek, Butcher of Truth", "roe"),
    ("Snow-Covered Forest", "khm"),
    ("Island", "m21"),
    ("Mountain", "m21"),
    ("Black Lotus", "lea"),
    ("Birds of Paradise", "m12"),
    ("Llanowar Elves", "dom"),
    ("Thoughtseize", "ths"),
    ("Snapcaster Mage", "isd"),
    ("Liliana of the Veil", "isd"),
    ("Path to Exile", "con"),
    ("Cryptic Command", "lrw"),
    ("Lightning Helix", "rav"),
    ("Nicol Bolas, Dragon-God", "war"),
    ("The Ur-Dragon", "c17"),
    ("Atraxa, Praetors' Voice", "c16"),
    ("Wurmcoil Engine", "som"),
    ("Karn Liberated", "nph"),
    ("Urza's Saga", "mh2"),
    ("Dryad Arbor", "fut"),
    ("Brazen Borrower", "eld"),
    ("Ragavan, Nimble Pilferer", "mh2"),
    ("Questing Beast", "eld"),
    ("Akroma, Angel of Wrath", "lgn"),
    ("Progenitus", "con"),
    ("Sword of Fire and Ice", "dst"),
    ("Mox Opal", "som"),
    ("Chalice of the Void", "mrd"),
    ("Blood Moon", "8ed"),
    ("Rest in Peace", "rtr"),
    ("Omnath, Locus of Creation", "znr"),
    ("Uro, Titan of Nature's Wrath", "thb"),
    ("Teferi, Hero of Dominaria", "dom"),
    ("Lotus Petal", "tmp"),
    ("Brainstorm", "ice"),
    ("Force of Will", "all"),
]

# (search query, how many results to keep in collector-number order; None = all)
SEARCHES: list[tuple[str, int | None]] = [
    ("e:arn", None),  # one complete small set, for set-completion tests
    ("e:dmu", 100),  # a slice of a modern set
    ("e:cmr is:etched", 1),  # etched-foil printing
    ("e:j21", 2),  # digital-only printings
    ("e:tdom", 2),  # tokens
]


def _checked(response: httpx.Response) -> dict:
    if response.status_code != 200:
        sys.exit(f"{response.status_code} for {response.url}: {response.text[:200]}")
    return response.json()


def get(client: httpx.Client, url: str, **params: str) -> dict:
    time.sleep(1)  # be gentle: Scryfall rate-limits aggressively
    return _checked(client.get(url, params=params))


def named(client: httpx.Client) -> list[dict]:
    """Fetch NAMED through the batch endpoint (75 identifiers per request)."""
    cards: list[dict] = []
    for start in range(0, len(NAMED), 75):
        batch = [{"name": name, "set": code} for name, code in NAMED[start : start + 75]]
        time.sleep(1)
        result = _checked(client.post(f"{API}/cards/collection", json={"identifiers": batch}))
        cards.extend(result["data"])
        for missing in result.get("not_found", []):
            # the batch endpoint can miss names that /cards/named resolves (e.g. one face)
            cards.append(
                get(client, f"{API}/cards/named", exact=missing["name"], set=missing["set"])
            )
    return cards


def search(client: httpx.Client, query: str, limit: int | None) -> list[dict]:
    cards: list[dict] = []
    page = get(
        client,
        f"{API}/cards/search",
        q=query,
        unique="prints",
        order="set",
        include_extras="true",
        include_variations="true",
    )
    while True:
        cards.extend(page["data"])
        if not page.get("has_more") or (limit and len(cards) >= limit):
            break
        page = get(client, page["next_page"])
    return cards[:limit] if limit else cards


def dump(path: Path, items: list[dict]) -> None:
    """One object per line: valid JSON, and readable diffs."""
    lines = [
        json.dumps(i, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for i in items
    ]
    path.write_text("[\n" + ",\n".join(lines) + "\n]\n", encoding="utf-8")


def main() -> None:
    by_id: dict[str, dict] = {}
    with httpx.Client(headers=HEADERS, timeout=30) as client:
        for card in named(client):
            by_id[card["id"]] = card
        for query, limit in SEARCHES:
            for card in search(client, query, limit):
                by_id[card["id"]] = card
        all_sets = get(client, f"{API}/sets")["data"]

    cards = sorted(by_id.values(), key=lambda c: (c["set"], c["collector_number"], c["id"]))
    codes = {c["set"] for c in cards}
    sets = sorted((s for s in all_sets if s["code"] in codes), key=lambda s: s["code"])

    OUT.mkdir(parents=True, exist_ok=True)
    dump(OUT / "cards.json", cards)
    dump(OUT / "sets.json", sets)
    print(f"{len(cards)} cards, {len(sets)} sets -> {OUT}")


if __name__ == "__main__":
    main()
