# 012. Read PDF, DOCX and XLSX

- id: 012
- status: seed
- source: own
- value: high

## Why

LUNA is an assistant for documents and reports, but `read` handles plain text only. A PDF,
a Word file or a spreadsheet fails with `не текстовый файл или неверная кодировка`. These are
the formats most work documents come in.

## What changes

Not decided yet. Open questions:

- **Where conversion happens.** Inside `read` by file extension, or in a separate
  `extract(path)` tool.
- **What structure survives.** Pages for PDF, headings and tables for DOCX, sheets and cell
  values for XLSX.
- **Large files.** The converted text goes through the same windowing and truncation as
  any other output.
- **Dependencies.** Which libraries, and whether they are an optional extra.

## Why this is LUNA

The files stay ordinary files in the zone. Conversion is a read-time view, so no special
format or domain schema is introduced.

## Out of scope

- Writing or editing binary formats.
- OCR of scanned documents.
