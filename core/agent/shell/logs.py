"""Capped binary capture and bounded, byte-accurate shell output pages."""

import codecs
import os
from dataclasses import dataclass
from pathlib import Path

from core.agent.shell.models import ShellError
from core.agent.tools.tool_output import MAX_BYTES, OutputTruncator

MAX_LOG_BYTES = 50 * 1024 * 1024

_PAGE_TRUNCATOR = OutputTruncator("head")


@dataclass(frozen=True, slots=True)
class OutputPage:
    output: str
    next_offset: int
    eof: bool
    has_more: bool


class CappedLog:
    """A single-writer binary log with a hard size limit."""

    def __init__(self, path: Path, *, max_bytes: int = MAX_LOG_BYTES) -> None:
        if max_bytes <= 0:
            raise ShellError("Log size limit must be positive")

        self.path = path
        self._max_bytes = max_bytes
        self._bytes_written = 0
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._stream = path.open("xb")
        except OSError as exc:
            raise ShellError(f"Cannot create shell log: {path}") from exc

    @property
    def bytes_written(self) -> int:
        return self._bytes_written

    def write(self, data: bytes) -> bool:
        """Write what fits; return True if any input exceeded the cap."""
        if self._stream.closed:
            raise ShellError("Shell log is already closed")

        remaining = self._max_bytes - self._bytes_written
        chunk = data[:remaining]
        try:
            if chunk:
                written = self._stream.write(chunk)
                self._bytes_written += written
                self._stream.flush()
                if written != len(chunk):
                    raise ShellError("Incomplete write to shell log")
        except OSError as exc:
            raise ShellError(f"Cannot write shell log: {self.path}") from exc
        return len(data) > remaining

    def close(self) -> None:
        if self._stream.closed:
            return
        try:
            self._stream.close()
        except OSError as exc:
            raise ShellError(f"Cannot close shell log: {self.path}") from exc


def _validate_boundary(context: bytes, boundary: int) -> None:
    """Reject a requested offset inside a complete UTF-8 character."""
    for start in range(max(0, boundary - 3), boundary):
        last = min(start + 4, len(context))
        for end in range(boundary + 1, last + 1):
            try:
                character = context[start:end].decode("utf-8")
            except UnicodeDecodeError:
                continue
            if len(character) == 1:
                raise ShellError("offset points inside a UTF-8 character")


def _decode_tokens(raw: bytes, *, final: bool) -> list[tuple[str, int]]:
    """Pair decoded fragments with the source-byte positions they consume."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    tokens: list[tuple[str, int]] = []
    for position, byte in enumerate(raw, start=1):
        text = decoder.decode(bytes((byte,)), final=False)
        if text:
            pending = len(decoder.getstate()[0])
            tokens.append((text, position - pending))
    tail = decoder.decode(b"", final=final)
    if tail:
        tokens.append((tail, len(raw)))
    return tokens


def read_page(path: Path, *, offset: int = 0, finished: bool) -> OutputPage:
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ShellError("offset must be a nonnegative integer")
    try:
        with path.open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            if offset > size:
                raise ShellError("offset is beyond the end of the log")
            start = max(0, offset - 3)
            prefix_size = offset - start
            window_size = min(MAX_BYTES, size - offset)
            stream.seek(start)
            context = stream.read(prefix_size + window_size)
    except OSError as exc:
        raise ShellError(f"Cannot read shell log: {path}") from exc

    _validate_boundary(context, prefix_size)
    raw = context[prefix_size:]
    at_file_end = offset + len(raw) == size
    tokens = _decode_tokens(raw, final=finished and at_file_end)
    text = "".join(fragment for fragment, _ in tokens)
    cut = _PAGE_TRUNCATOR.cut(text)
    shown_limit = len(cut.text) if cut is not None else len(text)

    fragments: list[str] = []
    shown_characters = 0
    consumed_bytes = 0
    for fragment, source_end in tokens:
        if shown_characters + len(fragment) > shown_limit:
            break
        fragments.append(fragment)
        shown_characters += len(fragment)
        consumed_bytes = source_end

    has_more = shown_characters < len(text) or not at_file_end
    return OutputPage(
        output="".join(fragments),
        next_offset=offset + consumed_bytes,
        eof=not has_more,
        has_more=has_more,
    )
