"""Tokenizer and parser (SPEC §6.1)."""

import pytest

from magicdb.search.errors import QueryError
from magicdb.search.parser import MAX_TERMS, And, Filter, Not, Or, Word, parse
from magicdb.search.tokenizer import MAX_LENGTH, OPERATORS


def f(key: str, value: str, op: str = ":") -> Filter:
    return Filter(key, op, value)


def test_empty_search_matches_everything():
    assert parse("") == And(())
    assert parse("   ") == And(())


def test_words_and_filters():
    assert parse("bolt") == Word("bolt")
    assert parse("lightning bolt") == And((Word("lightning"), Word("bolt")))
    assert parse('"lightning bolt"') == Word("lightning bolt")
    assert parse("t:dragon") == f("t", "dragon")
    assert parse('o:"draw a card" bolt') == And((f("o", "draw a card"), Word("bolt")))
    assert parse("T:Dragon") == f("t", "Dragon")  # keys are case-insensitive, values kept


@pytest.mark.parametrize("op", OPERATORS)
def test_every_operator(op):
    assert parse(f"mv{op}3") == f("mv", "3", op)


def test_exact_name():
    assert parse("!bolt") == Word("bolt", exact=True)
    assert parse('!"Lightning Bolt"') == Word("Lightning Bolt", exact=True)
    assert parse("!t:x") == Word("t:x", exact=True)  # "!" makes it a name, not a filter


def test_or_binds_looser_than_and():
    assert parse("a b or c") == Or((And((Word("a"), Word("b"))), Word("c")))
    assert parse("a or b or c") == Or((Word("a"), Word("b"), Word("c")))
    assert parse("a OR b") == Or((Word("a"), Word("b")))
    assert parse('"or"') == Word("or")  # quoted: a name, not the keyword
    assert parse("a and b") == And((Word("a"), Word("b")))


def test_parentheses():
    assert parse("(a or b) c") == And((Or((Word("a"), Word("b"))), Word("c")))
    assert parse("((a))") == Word("a")
    assert parse("a (b or (c d))") == And((Word("a"), Or((Word("b"), And((Word("c"), Word("d")))))))


def test_negation():
    assert parse("-t:land") == Not(f("t", "land"))
    assert parse("-bolt") == Not(Word("bolt"))
    assert parse("--a") == Not(Not(Word("a")))
    assert parse("-(a or b)") == Not(Or((Word("a"), Word("b"))))
    assert parse('-"two words"') == Not(Word("two words"))
    assert parse("a -b") == And((Word("a"), Not(Word("b"))))
    assert parse("x-wing") == Word("x-wing")  # a dash inside a word is just a dash


def test_values_keep_symbols():
    assert parse("m:{2}{R}{R}") == f("m", "{2}{R}{R}")
    assert parse("date>=2024-01-01") == f("date", "2024-01-01", ">=")
    assert parse("(t:elf)") == f("t", "elf")  # the value stops at the parenthesis
    assert parse('loc:"blue box (old)"') == f("loc", "blue box (old)")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('"abc', "never closed"),
        ('o:"abc', "never closed"),
        ("t:", "needs a value"),
        ('t:""', "needs a value"),
        ("mv>=", "needs a value"),
        ("-", "must be followed"),
        ("a - b", "must be followed"),
        ("(-)", "must be followed"),
        ("-or a", "must be followed"),
        ("(a", "never closed"),
        ("(", "never closed"),
        ("a (b or c", "never closed"),
        ("a)", "without a matching"),
        (")", "without a matching"),
        ("()", "nothing between"),
        ("a ()", "nothing between"),
        ("or a", "both sides"),
        ("a or", "both sides"),
        ("a or or b", "both sides"),
        ("x" * (MAX_LENGTH + 1), "too long"),
        ("a " * (MAX_TERMS + 1), "Too many"),
        ("(" * 30 + "a" + ")" * 30, "nested too deeply"),
        ("-" * 30 + "a", "nested too deeply"),
    ],
)
def test_errors(text, message):
    with pytest.raises(QueryError, match=message):
        parse(text)
