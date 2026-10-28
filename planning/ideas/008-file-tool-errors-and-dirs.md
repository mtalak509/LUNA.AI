# 008. Honest file tool errors and directory removal

- id: 008
- status: shaped
- source: own
- value: medium

## Why

The agent corrects itself from the text of a tool error, so that text has to name the real
cause. Today three errors name the wrong one:

- `rm` on a directory says `файл не найден`, although the directory exists.
- `find` on a missing path says `не каталог`, which sends the agent looking for a file with
  that name.
- `ls` on a file says `директория не найдена`, although the path exists.

There is also a gap. `write` creates parent directories on its own, but nothing can remove a
directory afterwards. A test run in the CLI left an empty `artifacts/sub/` behind for good.

`find` returns files only. On a zone root that holds only directories, `find("*")` answers
`(ничего не найдено)`. That is correct, but the agent read it as "the zone is empty". The tool
description does not say that directories are skipped.

## What changes

- `rm` removes an empty directory. A non-empty directory gets an error that says so.
- Each of `rm`, `find`, `ls` tells apart "path does not exist", "this is a file", and "this
  is a directory" in its error text.
- The `find` description states that it returns files only, never directories.

## Why this is LUNA

These are the general-purpose file tools over the working zone. The change keeps them
predictable and keeps the zone cleanable without new tools.

## Out of scope

- Recursive removal of non-empty directories.
- A `type` filter on `find` that returns directories. Add it if a real task needs it.
