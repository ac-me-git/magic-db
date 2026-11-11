"""The filter panel's query builder (SPEC §6.4)."""

import pytest

from magicdb.search.panel import build_query
from magicdb.search.parser import parse


def fields(**values: str | list[str]) -> dict[str, list[str]]:
    return {key: value if isinstance(value, list) else [value] for key, value in values.items()}


CASES = [
    ({}, ""),
    (fields(name="bolt"), "bolt"),
    (fields(name="  serra   angel "), "serra angel"),
    (fields(name="fire or ice"), 'fire "or" ice'),
    (fields(name="x-wing t:elf"), '"x-wing" "t:elf"'),
    (fields(text="draw a card"), 'o:"draw a card"'),
    (fields(text="flying"), "o:flying"),
    (
        fields(type="Legendary Creature", subtype="Elder Dragon"),
        "t:Legendary t:Creature t:Elder t:Dragon",
    ),
    (fields(keyword="First strike"), 'kw:"First strike"'),
    (fields(c=["g", "r"]), "c:rg"),
    (fields(c=["w", "u"], color_mode="eq"), "c=wu"),
    (fields(c=["b"], color_mode="le"), "c<=b"),
    (fields(c=["c"]), "c:c"),
    (fields(c=["c", "r"], color_mode="eq"), "c=r"),
    (fields(id=["w", "u", "b"]), "id:wub"),
    (fields(id=["c"]), "id:c"),
    (fields(mv_min="2", mv_max="4"), "mv>=2 mv<=4"),
    (fields(mv_min="3", mv_max="3"), "mv=3"),
    (fields(mv_max="1"), "mv<=1"),
    (fields(pow_min="5", tou_max="2"), "pow>=5 tou<=2"),
    (fields(mv_min="lots", mv_max="-1"), ""),
    (fields(r=["m"]), "r:m"),
    (fields(r=["u", "c", "x"]), "(r:c or r:u)"),
    (fields(set="MH3", format="modern"), "set:mh3 f:modern"),
    (fields(set="mh3 or 1=1"), ""),
    (fields(owned="1", location="Blue box"), 'have:yes loc:"Blue box"'),
    (fields(location="Deck (draghi)"), 'loc:"Deck (draghi)"'),
    (fields(unique="prints", order="price", dir="asc"), "unique:prints order:price dir:asc"),
    (fields(unique="cards", order="name", dir=""), ""),
    (fields(order="nonsense", dir="sideways", color_mode="??"), ""),
    (
        fields(
            name="dragon",
            text="flying",
            type="Creature",
            c=["r"],
            color_mode="ge",
            mv_max="5",
            r=["r", "m"],
            format="commander",
            owned="1",
            order="mv",
        ),
        "dragon o:flying t:Creature c:r mv<=5 (r:r or r:m) f:commander have:yes order:mv",
    ),
]


def test_there_are_enough_cases():
    assert len(CASES) >= 10


@pytest.mark.parametrize(
    ("submitted", "expected"), CASES, ids=lambda v: v if isinstance(v, str) else None
)
def test_build_query(submitted, expected):
    assert build_query(submitted) == expected
    parse(expected)  # whatever the panel writes, the parser reads


NASTY = [
    '"',
    '""',
    "(",
    ")",
    "-",
    "!",
    ":",
    "or",
    "a:b",
    'x" or t:land',
    "((",
    "-(",
    "\x00",
    "'; --",
]


@pytest.mark.parametrize("value", NASTY)
def test_any_field_value_still_gives_a_readable_query(value):
    for field in ("name", "text", "type", "subtype", "keyword", "location", "set", "format"):
        query = build_query(fields(**{field: value}))
        parse(query)
    parse(build_query(fields(c=[value], id=[value], r=[value], mv_min=value, order=value)))


def test_empty_and_missing_fields_are_ignored():
    assert build_query({"name": [], "c": [], "text": [""], "owned": [""]}) == ""
