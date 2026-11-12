"""Read a deck list written as text (SPEC §14).

Understands what Arena, MTGO, Moxfield and most deck sites export:

    4 Lightning Bolt
    4x Lightning Bolt
    1 Sol Ring (C21) 263
    1 Sol Ring [C21] *F*
    Sideboard
    2 Pyroblast
    SB: 1 Red Elemental Blast

This module only reads the text; whether a name is a real card is decided by the
caller, which has the catalog.
"""

import re
from dataclasses import dataclass

MAX_QUANTITY = 999

_QUANTITY = re.compile(r"(\d{1,4})\s*[xX]?\s+(\S.*)")
_SIDE_PREFIX = re.compile(r"sb:\s*", re.IGNORECASE)
_FINISH_MARK = re.compile(r"\s+\*[A-Za-z]+\*$")
_TAGS = re.compile(r"\s+#\S.*$")
_PRINTING = re.compile(r"\s+[(\[][A-Za-z0-9]{2,6}[)\]](\s+[\w★†-]+)?$")
_SLASHES = re.compile(r"\s*/{1,2}\s*")
_HEADER = re.compile(r"([a-z ]+?)\s*(\(\d+\))?:?")

MAIN_HEADERS = {"deck", "main", "maindeck", "main deck", "mainboard", "commander", "companion"}
SIDE_HEADERS = {"sideboard", "side", "side board", "sb"}
SKIPPED_HEADERS = {"maybeboard", "maybe", "considering", "tokens", "about"}
# words sites use to group a list by type: "Creatures (12)"
GROUP_HEADERS = {
    "creature",
    "creatures",
    "land",
    "lands",
    "instant",
    "instants",
    "sorcery",
    "sorceries",
    "artifact",
    "artifacts",
    "enchantment",
    "enchantments",
    "planeswalker",
    "planeswalkers",
    "battle",
    "battles",
    "spell",
    "spells",
    "other",
    "others",
    "other spells",
    "nonland",
    "nonlands",
}


@dataclass(frozen=True)
class Line:
    number: int  # line in the text, from 1
    text: str  # the line as written
    quantity: int
    name: str  # cleaned: no set, collector number or foil mark
    board: str  # "main" | "side"
    counted: bool  # the line gave a quantity ("4 Bolt"), not just a name


def clean_name(name: str) -> str:
    """'Sol Ring (C21) 263 *F*' -> 'Sol Ring'; 'Fire / Ice' -> 'Fire // Ice'."""
    name = _TAGS.sub("", name.strip())
    while True:
        shorter = _PRINTING.sub("", _FINISH_MARK.sub("", name))
        if shorter == name:
            break
        name = shorter
    return _SLASHES.sub(" // ", name).strip()


def header(line: str) -> str | None:
    """'main', 'side', 'skip' or 'group' if the line is a section title, else None."""
    match = _HEADER.fullmatch(line.strip().lower())
    if not match:
        return None
    word = match.group(1).strip()
    if word in MAIN_HEADERS:
        return "main"
    if word in SIDE_HEADERS:
        return "side"
    if word in SKIPPED_HEADERS:
        return "skip"
    if word in GROUP_HEADERS:
        return "group"
    return None


def parse(text: str) -> list[Line]:
    """The card lines of a deck list, in order. Titles, comments and the cards of
    sections that are not part of the deck (maybeboard, tokens) are left out."""
    lines: list[Line] = []
    board, skipping = "main", False
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(("//", "#")):
            continue
        kind = header(line)
        if kind is not None:
            if kind != "group":  # a type group keeps the section it is in
                skipping = kind == "skip"
                board = "side" if kind == "side" else "main"
            continue
        if skipping:  # also Arena's "About" block, whose next line is "Name <deck name>"
            continue
        line_board = board
        if _SIDE_PREFIX.match(line):
            line_board, line = "side", _SIDE_PREFIX.sub("", line, count=1)
        counted = _QUANTITY.fullmatch(line)
        quantity = int(counted.group(1)) if counted else 1
        name = clean_name(counted.group(2) if counted else line)
        if name:
            lines.append(Line(number, raw.strip(), quantity, name, line_board, bool(counted)))
    return lines
