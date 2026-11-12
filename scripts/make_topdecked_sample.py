"""Write tests/fixtures/topdecked-sample.csv.

The sample reproduces, byte for byte in structure, a real TopDecked binder export
(header, quoting, the unquoted decimal comma in prices, the missing trailing columns),
but its rows are cards of the test fixture rather than anyone's collection.

    python scripts/make_topdecked_sample.py
"""

import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
HEADER = (
    'QUANTITY,"NAME",SETCODE,"SETNAME","COLLECTOR NUMBER",FINISH,PRICE,RARITY,ID,'
    "ACQUIRED DATE,ACQUIRED PRICE,LANG,PRICE SALE,SIGNING,ALTERATION,CONDITION,NOTES,TAGS"
)
# (name as TopDecked writes it, set, finish) — one row per copy, as in the real export
PICKS = [
    ("Lightning Bolt", "m10", "nonfoil"),
    ("Lightning Bolt", "m10", "nonfoil"),
    ("Lightning Bolt", "m10", "foil"),
    ("Lightning Bolt", "lea", "nonfoil"),
    ("Serra Angel", "dom", "foil"),
    ("Sol Ring", "c21", "nonfoil"),
    ("Jace, the Mind Sculptor", "wwk", "nonfoil"),
    ("Delver of Secrets // Insectile Aberration", "isd", "nonfoil"),
    ("Fire // Ice", "apc", "nonfoil"),
    ("Black Lotus", "lea", "nonfoil"),
    ("Shivan Dragon", "m20", "nonfoil"),
    ("Niv-Mizzet Reborn", "war", "foil"),
]


def main() -> None:
    cards = json.loads((FIXTURES / "cards.json").read_text(encoding="utf-8"))
    by_key = {(c["name"], c["set"]): c for c in cards}
    chosen = [(by_key[(name, set_code)], finish) for name, set_code, finish in PICKS]
    arabian = sorted(
        (c for c in cards if c["set"] == "arn"),
        key=lambda c: (len(c["collector_number"]), c["collector_number"]),
    )
    chosen += [(card, "nonfoil") for card in arabian[:18]]

    lines = [HEADER]
    for index, (card, finish) in enumerate(chosen):
        price = card["prices"].get("eur_foil" if finish == "foil" else "eur") or "0.00"
        euros, _, cents = f"{float(price):.2f}".partition(".")
        lines.append(
            ",".join(
                [
                    "1",
                    f'"{card["name"]}"',
                    card["set"],
                    f'"{card["set_name"]}"',
                    card["collector_number"],
                    finish,
                    f"€{euros}",  # the decimal comma is not quoted: the price becomes
                    cents,  # two fields, exactly as in the real file
                    card["rarity"],
                    card["id"],
                    f"2025-02-11T15:31:46.{500 + index:03d}Z",
                ]
            )
        )
    path = FIXTURES / "topdecked-sample.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(lines) - 1} rows -> {path}")


if __name__ == "__main__":
    main()
