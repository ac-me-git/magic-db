# Decks

A deck in magic-db is a **list of cards you want to play together**. It is separate from
your collection: adding a card to a deck does not add it to the collection, and does not
move it anywhere. What the deck gives you is a comparison with what you own.

(If you want to record that some cards physically sit in a deck box, that is a *location*
of the collection, not a deck.)

## Making a deck

On the **Decks** page, give it a name and, if you like, a format and a note. Then add
cards in any of these ways:

- **One at a time**, on the deck's page: type the card's name (suggestions appear), the
  quantity, and whether it goes in the main deck or the sideboard.
- **From a card's page**: "Add to the deck".
- **By pasting a list**, on the deck's page.

### Deck lists

One card per line, as most sites and programs export them:

```
4 Lightning Bolt
4x Counterspell
1 Sol Ring (C21) 263
Brainstorm

Sideboard
2 Pyroblast
SB: 1 Red Elemental Blast
```

- The number is how many; a line with just a name is one copy.
- Set codes, collector numbers and foil marks (`(C21) 263`, `[C21]`, `*F*`) are ignored:
  a deck names cards, not printings.
- `Sideboard` on a line by itself starts the sideboard. `SB:` in front of a line puts
  that one line there. A blank line does **not** start the sideboard.
- `Deck`, `Commander` and `Companion` go back to the main deck. A `Maybeboard` section is
  skipped.
- Titles such as `Creatures (12)`, comments (`//`, `#`) and blank lines are ignored.

Lines that are not card names are shown to you with a suggestion and put back in the box
to correct; everything else is added.

## What the deck tells you

- **Completion**: the round gauge shows how many of the deck's cards you own, as a
  percentage. Any printing counts, wherever it is. 100 % means you have every copy.
- **Missing**: cards you do not own enough copies of for this deck.
- **Contested**: cards you own enough of for this deck alone, but not for all the decks
  that want them. If two decks each want 4 Lightning Bolt and you own 6, both decks are
  complete on their own and the card is contested.
- **To buy the missing**: an estimate, using the cheapest printing of each missing card.

The Decks page also has one list of every contested card: how many you own, how many
your decks want in total, and which decks.

## Statistics

Every deck has a second tab, **Statistics**, about its main deck:

- how many lands and how many other cards, and the average mana value of the latter;
- cards by type — an artifact creature counts as both;
- the mana curve and the colours, leaving lands out;
- the coloured mana symbols in the costs, which says how much of each colour the deck
  really asks for. A hybrid symbol counts for both its colours.

## Other things

- "See the cards as images" searches for `deck:"name"`; you can combine that filter with
  any other, for example `deck:dragons have:no` for what you still need.
- "Download as text" gives the list back in the format above.
- A card's page shows which of your decks it is in.
- Decks are included in the JSON export and in the nightly backup.
- While the safety lock is on, decks can be looked at but not changed.
