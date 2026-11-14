"""Split a search string into tokens (SPEC §6.1)."""

import re
from dataclasses import dataclass, field

from magicdb.search.errors import QueryError

MAX_LENGTH = 2000
OPERATORS = (":", "=", "!=", "<", "<=", ">", ">=")
_KEY_OP = re.compile(r"([A-Za-z]+)(!=|<=|>=|:|=|<|>)")
_BARE_END = re.compile(r"[\s()]")


@dataclass(frozen=True)
class Token:
    kind: str  # "lparen" | "rparen" | "neg" | "or" | "word" | "filter"
    value: str = ""
    key: str = ""
    op: str = ""
    exact: bool = False  # word only: !name or !"full name"
    # filter only: where it is in the text, text[span[0]:span[1]]
    span: tuple[int, int] = field(default=(0, 0), compare=False)


def _quoted(text: str, start: int) -> tuple[str, int]:
    end = text.find('"', start + 1)
    if end == -1:
        raise QueryError('A quote (") is never closed.')
    return text[start + 1 : end], end + 1


def _bare(text: str, start: int) -> tuple[str, int]:
    match = _BARE_END.search(text, start)
    end = match.start() if match else len(text)
    return text[start:end], end


def tokenize(text: str) -> list[Token]:
    if len(text) > MAX_LENGTH:
        raise QueryError(f"The search is too long (more than {MAX_LENGTH} characters).")
    tokens: list[Token] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "(":
            tokens.append(Token("lparen"))
            i += 1
            continue
        if ch == ")":
            tokens.append(Token("rparen"))
            i += 1
            continue
        if ch == "-":
            if i + 1 >= n or text[i + 1].isspace() or text[i + 1] == ")":
                raise QueryError("A '-' must be followed by what to exclude, as in -t:land.")
            tokens.append(Token("neg"))
            i += 1
            continue

        exact = False
        if ch == "!" and i + 1 < n and not text[i + 1].isspace():
            exact = True
            i += 1
            ch = text[i]

        if ch == '"':
            value, i = _quoted(text, i)
            if value.strip():
                tokens.append(Token("word", value=value.strip(), exact=exact))
            continue

        match = None if exact else _KEY_OP.match(text, i)
        if match:
            key, op = match.group(1).lower(), match.group(2)
            start, i = i, match.end()
            if i < n and text[i] == '"':
                value, i = _quoted(text, i)
            else:
                value, i = _bare(text, i)
            if not value.strip():
                raise QueryError(f"'{key}{op}' needs a value after it.")
            tokens.append(Token("filter", value=value.strip(), key=key, op=op, span=(start, i)))
            continue

        value, i = _bare(text, i)
        lowered = value.lower()
        if not exact and lowered == "or":
            tokens.append(Token("or"))
        elif not exact and lowered == "and":
            pass  # terms are joined by "and" anyway
        else:
            tokens.append(Token("word", value=value, exact=exact))
    return tokens
