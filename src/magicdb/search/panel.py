"""The filter panel: turn form fields into a search string (SPEC §6.4).

The panel never searches by itself. It writes a query in the same language the search
box accepts, so there is one search engine and the generated text teaches the syntax.
"""

import re
from collections.abc import Mapping, Sequence

from magicdb.search.compiler import ORDERS

Fields = Mapping[str, Sequence[str]]

COLOR_ORDER = "wubrg"
COLOR_MODES = {"ge": ":", "eq": "=", "le": "<="}
RARITIES = ("c", "u", "r", "m")

_PLAIN_WORD = re.compile(r"[\w',.]+")
_NUMBER = re.compile(r"\d{1,4}(\.\d)?")
_CODE = re.compile(r"[a-z0-9]{1,10}")
_KEYWORDS = ("or", "and")


def _one(fields: Fields, key: str) -> str:
    values = fields.get(key) or [""]
    return values[0].strip()


def _clean(value: str) -> str:
    """Drop the one character a value can never contain: there is no way to escape it."""
    return " ".join(value.replace('"', " ").split())


def _word(value: str) -> str:
    """A name word, quoted when the parser would otherwise read it as syntax."""
    if _PLAIN_WORD.fullmatch(value) and value.lower() not in _KEYWORDS:
        return value
    return f'"{value}"'


def _value(value: str) -> str:
    """A filter value, quoted when it has spaces or parentheses."""
    return f'"{value}"' if re.search(r"[\s()]", value) else value


def _range(fields: Fields, key: str, low: str, high: str) -> list[str]:
    lowest, highest = _one(fields, low), _one(fields, high)
    lowest = lowest if _NUMBER.fullmatch(lowest) else ""
    highest = highest if _NUMBER.fullmatch(highest) else ""
    if lowest and lowest == highest:
        return [f"{key}={lowest}"]
    return [f"{key}>={lowest}"] * bool(lowest) + [f"{key}<={highest}"] * bool(highest)


def _colors(fields: Fields, field: str, key: str, op: str) -> list[str]:
    chosen = {value.lower() for value in fields.get(field) or ()}
    letters = "".join(letter for letter in COLOR_ORDER if letter in chosen)
    if letters:
        return [f"{key}{op}{letters}"]
    return [f"{key}:c"] if "c" in chosen else []


def build_query(fields: Fields) -> str:
    """Build the search string for the filter panel's fields.

    ``fields`` maps a field name to its submitted values (several for checkboxes).
    Whatever is submitted, the result is text the search parser accepts.
    """
    terms: list[str] = []

    terms += [_word(word) for word in _clean(_one(fields, "name")).split()]
    if text := _clean(_one(fields, "text")):
        terms.append(f"o:{_value(text)}")
    for field in ("type", "subtype"):
        terms += [f"t:{_value(word)}" for word in _clean(_one(fields, field)).split()]
    if keyword := _clean(_one(fields, "keyword")):
        terms.append(f"kw:{_value(keyword)}")

    mode = COLOR_MODES.get(_one(fields, "color_mode"), ":")
    terms += _colors(fields, "c", "c", mode)
    terms += _colors(fields, "id", "id", ":")

    terms += _range(fields, "mv", "mv_min", "mv_max")
    terms += _range(fields, "pow", "pow_min", "pow_max")
    terms += _range(fields, "tou", "tou_min", "tou_max")

    rarities = [r for r in RARITIES if r in {v.lower() for v in fields.get("r") or ()}]
    if len(rarities) == 1:
        terms.append(f"r:{rarities[0]}")
    elif rarities:
        terms.append("(" + " or ".join(f"r:{r}" for r in rarities) + ")")

    set_code = _one(fields, "set").lower()
    if _CODE.fullmatch(set_code):
        terms.append(f"set:{set_code}")
    card_format = _one(fields, "format").lower()
    if _CODE.fullmatch(card_format):
        terms.append(f"f:{card_format}")

    if _one(fields, "owned"):
        terms.append("have:yes")
    if location := _clean(_one(fields, "location")):
        terms.append(f"loc:{_value(location)}")

    if _one(fields, "unique") == "prints":
        terms.append("unique:prints")
    order = _one(fields, "order")
    if order in ORDERS and order != "name":
        terms.append(f"order:{order}")
    if (direction := _one(fields, "dir")) in ("asc", "desc"):
        terms.append(f"dir:{direction}")

    return " ".join(terms)
