# Import and export

Both are on the **My collection** page.

## Export

| Format | What is in it | Use it for |
|---|---|---|
| **CSV** | One row per printing and finish you own: card name, set, collector number, Scryfall ID, quantity, finish, notes, price paid, date added | Spreadsheets, other tools, a copy you can read without magic-db |
| **JSON** | Everything: every entry and every deck | A complete copy to bring back later, or to move to another installation |

A single deck can also be downloaded as a text list from its page.

## Import

Importing is two steps. First magic-db reads the file and shows what it understood —
**nothing is written**. Then, if you confirm, it saves a backup and imports.

On the preview page you choose whether to **add** to what you already have, or to
**replace** it. magic-db keeps, for each printing, how many copies you own and in which
finish: what a file says about condition, language or where a card is kept is not read,
and rows that differ only in those become one.

Rows that cannot be imported are listed with the reason, and can be downloaded as a CSV
to fix and import again. They never stop the other rows.

Importing the same file twice adds the cards twice. The switch must be on *Collection input*.

### Formats understood

| Source | Notes |
|---|---|
| **magic-db** | The CSV and JSON files made by Export |
| **TopDecked** | The CSV export of a binder (one row per copy) |
| **Moxfield** | Collection export (CSV) |
| **ManaBox** | Collection export (CSV) |
| **Any CSV** | With at least a `Name` column, and optionally `Set code`, `Collector number`, `Scryfall ID`, `Quantity`, `Finish`, `Notes`, `Purchase price`, `Added` |

Exports made by magic-db before the collection was simplified (with Condition, Language
and Location columns, or a JSON with locations) import too.

The Moxfield and ManaBox formats follow those tools' documented column names and have not
been tried on real files yet. If one of yours is not read correctly, please open an issue
with a few rows of it.

### How a row finds its card

In this order, stopping at the first that works:

1. the Scryfall ID;
2. set code and collector number;
3. card name and set code;
4. card name and set name;
5. card name alone — only if the card has a single printing.

A name that matches several printings with nothing to tell them apart is **ambiguous** and
is not imported. If the set is known but has several versions of the card (showcase,
borderless) and the file gives no number, the regular one is taken and the row is counted
as *guessed*.

### File details

UTF-8 (with or without BOM) and Windows-1252 are read; fields can be separated by commas,
semicolons or tabs; files up to 20 MB.
