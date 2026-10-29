---
type: tool reference
title: File tools and output truncation
description: The seven general-purpose file tools (read, write, edit, ls, find, grep, rm), their exact semantics and errors, and how large tool output is cut and spilled to .truncated_outputs/ so one call cannot flood the context.
tags: [tools, files, truncation, editing, search]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-29T17:46:46.267Z
sources:
  - id: openwiki-source-e1f3d5a721b9517f9aa6ba09
    resource: repo://core/agent/tools/files_tools.py
  - id: openwiki-source-ab5a4aaa5b635da70e735b12
    resource: repo://core/agent/tools/tool_output.py
generated: { by: "claude-code", at: "2026-10-29T17:46:46.267Z" }
---

# File tools and output truncation

LUNA works with the user's files by path. Seven ordinary tools cover that work
(`core/agent/tools/files_tools.py`). They mirror the Read/Write/Edit/Glob/Grep tools of coding
agents, so models already know them, but they are real tools rather than shell commands. That is
what lets LUNA attach zone checks, checkpoints and output limits to each one. There is no special
document format: a JSON or CSV file is just another text file.

All paths are relative. Each tool builds a `PathScope` from the runtime context, so the same tool
works in the main agent's `workspace/` or in a subagent's zone (see
[zones and PathScope](../concepts/zones-and-path-scope.md)). Files are read and written as UTF-8.

## The tools

| Tool | Writes | Behaviour |
|---|---|---|
| `read(path, offset?, limit?)` | no | Whole file without a window. With `offset` (first line, from 1) and/or `limit` (line count) returns that window headed `[строки X–Y из N]`. |
| `write(path, content)` | yes | Create or overwrite a whole file; parent folders are created. Returns `Записано: <path> (<n> символов)`. |
| `edit(path, old_string, new_string, replace_all=False)` | yes | Replace text in an existing file. `old_string` must occur **exactly once** unless `replace_all=True`. |
| `ls(path=".")` | no | One directory level: folders first (with `/`), then files; `(пусто)` when empty. |
| `find(pattern, path=".")` | no | Files by glob (`*.md`, `**/*.md`, `*report*`), sorted, relative to `path`. |
| `grep(pattern, path=".", glob?, ignore_case=False)` | no | Lines matching a Python regex, as `path:line_number: text`. `path` may be a file or a folder; `glob` narrows the files. |
| `rm(path)` | yes | Delete one file. Folders are not deleted. |

`find` and `grep` print `(ничего не найдено)` when nothing matches.

### Errors the model sees

Every expected problem raises `FileAccessError` (a `ToolException`), which the tool-error
middleware returns to the model as an error result to correct from. Error texts are Russian.
Examples:

- missing file or folder, or a folder passed where a file is expected;
- a non-UTF-8 or binary file in `read` / `edit`;
- `offset`/`limit` below 1, or `offset` past the end of the file (the message gives the line
  count);
- `edit` whose `old_string` is not found, or found several times without `replace_all` (the
  message says how many and suggests adding context);
- an invalid regex in `grep` or an invalid glob in `find`;
- any path refused by `PathScope`.

`grep` silently skips files it cannot decode, so a search over a mixed folder does not fail on a
binary file.

### Search never sees service folders

`find` and `grep` collect files through `_zone_files`, which keeps only files that resolve inside
the search root (a glob containing `..` cannot climb out) and drops anything under `.runtime/`
or `.truncated_outputs/`. `ls` hides those two names too. So the agent cannot find its own
history or its own earlier spilled output.

### Writes leave checkpoints

`write`, `edit` and `rm` are declared `is_write=True` and wrapped in `@with_checkpoint()`. Being
write tools, they are hidden and blocked in Plan mode, need approval in Confirm mode, and take a
workspace snapshot after each success (see [middleware](../architecture/middleware.md) and
[checkpoints](../concepts/checkpoints-and-undo.md)). None of the three may touch
`.truncated_outputs/`.

## The intended working pattern

Large files never need to enter the context whole:

1. `grep` for the place you need — it reports line numbers;
2. `read(path, offset=<line>, limit=<n>)` a window around it;
3. `edit` exactly that piece.

The main prompt teaches this pattern, and the live `text_editing` scenario checks that a
targeted change uses `edit` rather than rewriting the file with `write`.

## Output truncation

A single tool result is capped at **2000 lines or 50 KB (UTF-8 bytes), whichever comes first**
(`core/agent/tools/tool_output.py`).

### `@truncate_output` for `ls`, `find`, `grep`

Each tool declares its cut with a decorator placed inside `@agent_tool`:

- `head` keeps the beginning — right for search results and listings (used by all three today);
- `tail` keeps the end — meant for commands whose last lines matter most.

The strategy is explicit on the tool, never inferred from its name, so a new tool cannot silently
get the wrong cut. `OutputTruncator` cuts on line boundaries; only when a single line alone is
over the byte limit is it cut by bytes, and never inside a multi-byte character.

When output is cut, the **full** text is saved to
`<agent zone>/.truncated_outputs/<tool>-<12 hex>.txt` (the location comes from
`PathScope.spill_dir`), and a note is added — after the text for `head`, before it for `tail`:

```
[вывод обрезан: показано 2000 из 9184 строк, 81 КБ из 377 КБ.
 Полный вывод: .truncated_outputs/grep-1a2b3c4d5e6f.txt — читай его через read с offset/limit.]
```

The agent continues with `read` and `offset`/`limit` on that file, or narrows it with another
`grep`. If saving fails, the note says so and suggests narrowing the query; the tool still
returns the cut result. Errors raised by the tool pass through untouched.

### `read` cuts itself

`read` does not use the decorator, because its source is already a file and saving a copy would
be pointless. It selects the requested lines first, then `cut_window` cuts *those* lines and only
afterwards writes the header and the note. Both therefore name the lines actually shown, and the
note gives the exact `offset` to continue from:

```
[строки 1–2000 из 9184]
...
[вывод обрезан: показаны строки 1–2000 из 9184. Продолжай с offset=2001.]
```

A whole-file read that fits the limits is returned as is, with no header.

The live `truncation` scenarios check that the agent actually follows these notes instead of
answering from the partial output (see [tests and evals](../testing/tests-and-live-evals.md)).

## History

These tools replaced an earlier JSON-document tool layer and older names (`read_file`,
`write_file`, `edit_file`, `list_files`, `search_files`, `delete_file`); `search_files` was split
into `find` (names) and `grep` (content). See idea cards 001 and 002 in `planning/`.
