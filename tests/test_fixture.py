"""The fixture must contain every case listed in SPEC §12."""

from collections import Counter


def _types(card: dict) -> str:
    return card.get("type_line", "")


def _costs(card: dict) -> list[str]:
    costs = [card.get("mana_cost", "")]
    costs += [face.get("mana_cost", "") for face in card.get("card_faces", [])]
    return costs


def test_size(fixture_cards):
    assert 200 <= len(fixture_cards) <= 400
    assert len({c["id"] for c in fixture_cards}) == len(fixture_cards)


def test_layouts(fixture_cards):
    layouts = {c["layout"] for c in fixture_cards}
    assert {"transform", "modal_dfc", "split", "adventure", "saga", "token"} <= layouts


def test_card_types(fixture_cards):
    assert any("Planeswalker" in _types(c) for c in fixture_cards)
    assert any("Battle" in _types(c) for c in fixture_cards)


def test_star_power(fixture_cards):
    assert any("*" in (c.get("power") or "") for c in fixture_cards)


def test_colours(fixture_cards):
    assert any(
        "Artifact" in _types(c) and c.get("colors") == [] and c["color_identity"] == []
        for c in fixture_cards
    )
    assert any(len(c.get("colors", [])) == 5 for c in fixture_cards)
    assert any("{R/W}" in cost for c in fixture_cards for cost in _costs(c))


def test_finishes_and_games(fixture_cards):
    assert any("etched" in c["finishes"] for c in fixture_cards)
    assert any(c["digital"] for c in fixture_cards)


def test_double_faced_card_has_images_on_faces(fixture_cards):
    card = next(c for c in fixture_cards if c["layout"] == "transform")
    assert "image_uris" not in card
    assert all("image_uris" in face for face in card["card_faces"])


def test_card_with_many_printings(fixture_cards):
    printings = Counter(c["oracle_id"] for c in fixture_cards if "oracle_id" in c)
    assert max(printings.values()) >= 5


def test_complete_set(fixture_cards, fixture_sets):
    arn = next(s for s in fixture_sets if s["code"] == "arn")
    in_fixture = [c for c in fixture_cards if c["set"] == "arn"]
    assert len(in_fixture) == arn["card_count"]


def test_sets_cover_every_card(fixture_cards, fixture_sets):
    assert {c["set"] for c in fixture_cards} == {s["code"] for s in fixture_sets}
