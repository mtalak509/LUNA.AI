# Planning

Development ideas live here, next to the code. This folder is the decision log. It is not a
description of how LUNA works today (`openwiki/`), not a release digest (`digests/`), and
not a record of what already shipped (`CHANGELOG.md`).

## Layout

```
planning/
  README.md
  index.md
  ideas/
    001-short-name.md
```

One idea is one file. The number is permanent. Do not rename a file after it is created.
Cards are written in English. Identifiers and paths are English too.

## Card

```markdown
# 001. Short title

- id: 001
- status: shaped
- source: own
- value: high

## Why

## What changes

## Why this is LUNA

## Out of scope
```

`source` is `own` or a digest path such as `digests/1.2.0`.

## Status

- `seed` — a note, not ready to decide.
- `shaped` — why, boundary, and the LUNA check are written.
- `accepted` — we will do this.
- `building` — in the code.
- `done` — shipped. Detail stays in `CHANGELOG.md`.
- `parked` — deliberately deferred.
- `dropped` — we will not do it. The card stays so the question is not reopened.

## What gets a card

A card is a decision about what to build. A feature earns one when it makes LUNA easier to
understand, extend, or run locally. A digest path in `source` records where the note came
from. The card itself states the LUNA decision and does not depend on that note to be
understood.

`planning/index.md` is only the table: number, title, status, source. Update it in the same
change as the card.
