"""Turn tokens into a syntax tree (SPEC §6.1).

query := or
or    := and ( "or" and )*
and   := term+
term  := "-" term | "(" or ")" | filter | word
"""

from dataclasses import dataclass

from magicdb.search.errors import QueryError
from magicdb.search.tokenizer import Token, tokenize

MAX_DEPTH = 20
MAX_TERMS = 100


@dataclass(frozen=True)
class Word:
    text: str
    exact: bool = False


@dataclass(frozen=True)
class Filter:
    key: str
    op: str
    value: str


@dataclass(frozen=True)
class Not:
    node: "Node"


@dataclass(frozen=True)
class And:
    nodes: tuple["Node", ...]


@dataclass(frozen=True)
class Or:
    nodes: tuple["Node", ...]


Node = Word | Filter | Not | And | Or


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0
        self.depth = 0  # nesting of "-" and "(", to bound recursion
        self.parens = 0  # open parentheses, for error messages

    def peek(self) -> Token | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> Token:
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def parse_or(self) -> Node:
        nodes = [self.parse_and()]
        while (token := self.peek()) is not None and token.kind == "or":
            self.take()
            nodes.append(self.parse_and())
        return nodes[0] if len(nodes) == 1 else Or(tuple(nodes))

    def parse_and(self) -> Node:
        nodes: list[Node] = []
        while (token := self.peek()) is not None and token.kind not in ("rparen", "or"):
            nodes.append(self.parse_term())
        if not nodes:
            if token is None and self.parens:
                raise QueryError("A parenthesis is never closed.")
            if token is not None and token.kind == "rparen":
                if self.parens:
                    raise QueryError("There is nothing between the parentheses.")
                raise QueryError("There is a ')' without a matching '('.")
            raise QueryError("'or' needs something to search on both sides.")
        return nodes[0] if len(nodes) == 1 else And(tuple(nodes))

    def parse_term(self) -> Node:
        token = self.take()
        if token.kind not in ("neg", "lparen"):
            if token.kind == "filter":
                return Filter(token.key, token.op, token.value)
            return Word(token.value, token.exact)

        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise QueryError("The search is nested too deeply.")
        if token.kind == "neg":
            if self.peek() is None or self.peek().kind in ("or", "rparen"):
                raise QueryError("A '-' must be followed by what to exclude, as in -t:land.")
            node: Node = Not(self.parse_term())
        else:
            self.parens += 1
            node = self.parse_or()
            if self.peek() is None:
                raise QueryError("A parenthesis is never closed.")
            self.take()  # parse_or stops only at ")" here
            self.parens -= 1
        self.depth -= 1
        return node


def parse(text: str) -> Node:
    """Parse a search string. An empty search is ``And(())``: it matches everything."""
    tokens = tokenize(text)
    if sum(token.kind in ("word", "filter") for token in tokens) > MAX_TERMS:
        raise QueryError(f"Too many search terms (more than {MAX_TERMS}).")
    if not tokens:
        return And(())
    parser = _Parser(tokens)
    node = parser.parse_or()
    if parser.peek() is not None:
        raise QueryError("There is a ')' without a matching '('.")
    return node
