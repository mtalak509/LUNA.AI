# 001. File tools replace the JSON tool layer

- id: 001
- status: done
- source: own, digests/1.2.0
- value: high

## Why

LUNA still carries a JSON document layer (`json_inspect`, `json_search`, `json_patch`, and
`json_doc`) and a file layer with long names (`read_file`, `write_file`, `edit_file`,
`list_files`, `search_files`, `delete_file`). There is no canonical JSON document for the
JSON tools to serve. The document tab and the unused section pointer are already gone.

`search_files` also mixes two jobs: find a file by name, and find a line by content.

## What changes

Remove the JSON tool layer completely: the three tools, `json_doc`, their pool registration,
the procedure text that teaches them, the tools doc section, and the tests that exist only
for them.

The file tool set is:

| Tool | Job |
|------|-----|
| `read` | Read a text file. |
| `write` | Create or overwrite a file. |
| `edit` | Replace an exact piece of text. |
| `ls` | List one directory. |
| `find` | Find files by name. |
| `grep` | Find lines by content. |
| `rm` | Delete a file. |

JSON is an ordinary file. Find a field with `grep`, read a slice with `read`, change a
slice with `edit`, replace a file with `write`.

Keep the behavior LUNA already has: relative paths, zone scoping, checkpoints on writes,
`.runtime` hidden, and `edit` matching once unless replace-all is set. This card sets the
names and jobs. It does not adopt an argument contract from the digest.

`read` and `grep` depend on [002](002-truncate-tool-output.md). This card does not define
that decorator.

## Why this is LUNA

LUNA edits files the user brought, not a privileged document. One file vocabulary is easier
to understand and to extend than a pointer language plus a second set of file tools. A bad
`edit` can leave invalid JSON; the agent checks the file with the same tools. That is
acceptable because LUNA is not a structural JSON editor.

## Out of scope

- A process sandbox, a second transport, and tools for a domain document.
- How the truncation decorator works. That is card 002. This set only depends on it.
- Renaming checkpoints or rewriting changelog history that mentions `json_patch`.
