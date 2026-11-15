# Decks

A deck in magic-db is a **list of cards you want to play together**: a name, notes if
you like, and the cards with how many of each. There is no format and no sideboard. A
deck is separate from your collection: adding a card to a deck does not add it to the
collection, and does not take it out. What the deck gives you is a comparison with what
you own.

## Making a deck

Decks change only while the switch of what may change (above the search results, and
in the ☰ menu) is on **Deck building**; in the other positions the deck pages show a
button to move it there.

On the **Decks** page, press **New deck** (top right). The deck is called "Deck 1",
"Deck 2" and so on, and its page opens with the name selected: type the one you want
and, if you like, a note, then *Save*. They stay at the top of the deck's page, under
*Name and notes*. Then add cards in any of these ways:

- **One at a time**, on the deck's page: type the card's name (suggestions appear) and
  the quantity.
- **From a card's page**: "Add to the deck".
- **From a search, in deck mode**: set the switch above the results to **Deck
  building**, then search as usual (say `t:human c:wg`), as Images or as a List. Pick
  the deck at the top; on every card, + puts one more copy in it and − takes one out.
  On images the buttons are faint until you point at the card. The switch *Only cards in this deck* limits the
  search to the cards already there (it writes `deck:"name"` into the search box); with
  *Search my collection* (the ▾ beside Search) as well, you see only cards you own.
- **By pasting a list**, on the deck's page.

### Deck lists

One card per line, as most sites and programs export them:

```
1 Miirym, Sentinel Wyrm
4x Counterspell
1 Sol Ring (C21) 263
Brainstorm
```

- The number is how many; a line with just a name is one copy.
- Set codes, collector numbers and foil marks (`(C21) 263`, `[C21]`, `*F*`) are ignored:
  a deck names cards, not printings.
- A deck has no sideboard: the cards of a `Sideboard` section, or of a line starting
  with `SB:`, join the list like the others. A `Maybeboard` section is skipped.
- Titles such as `Deck`, `Commander`, `Creatures (12)`, comments (`//`, `#`) and blank
  lines are ignored.

Lines that are not card names are shown to you with a suggestion and put back in the box
to correct; everything else is added.

## What the deck tells you

- **Completion**: the round gauge shows how many of the deck's cards you own, as a
  percentage. Any printing counts, in any finish. 100 % means you have every copy.
- **Missing**: cards you do not own enough copies of for this deck.
- **Contested**: cards you own enough of for this deck alone, but not for all the decks
  that want them. If two decks each want 4 Lightning Bolt and you own 6, both decks are
  complete on their own and the card is contested.
- **Estimated cost**: what the whole deck would cost, using the cheapest paper printing
  of each card. **To buy the missing** is the same estimate for the cards you lack.
- **Colour identity**, as mana symbols next to its name: the colours of the deck's
  widest legendary creature — the one with most colours, in practice the commander. A
  deck with no legendary creature shows the colours of all its cards together. Two
  partner commanders of different colours show only the wider one.

The Decks page lists your decks one under the other, each with these facts, how many
cards it has, and the day it last changed. It also has one list of every contested
card: how many you own, how many your decks want in total, and which decks.

## Statistics

Every deck has a second tab, **Statistics**:

- how many lands and how many other cards, and the average mana value of the latter;
- cards by type — an artifact creature counts as both;
- the mana curve and the colours, leaving lands out;
- the coloured mana symbols in the costs, which says how much of each colour the deck
  really asks for. A hybrid symbol counts for both its colours.

## Other things

- "See the cards as images" searches for `deck:"name"`; you can combine that filter with
  any other, for example `deck:dragons have:no` for what you still need.
- "Download as text" gives the list back, one line per card.
- A card's page shows which of your decks it is in.
- Decks are included in the JSON export and in the nightly backup.
- Decks can be changed only with the switch on *Deck building*; otherwise they can be
  looked at but not changed.
