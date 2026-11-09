"""Bounded shell capture and byte-accurate output continuation."""

from pathlib import Path

import pytest

from core.agent.shell.logs import CappedLog, read_page
from core.agent.shell.models import ShellError


def test_writer_flushes_chunks_and_stops_at_cap(tmp_path: Path) -> None:
    path = tmp_path / "tasks" / "output.log"
    log = CappedLog(path, max_bytes=5)
    try:
        assert log.write(b"abc") is False
        assert path.read_bytes() == b"abc"
        assert log.write(b"defg") is True
        assert path.read_bytes() == b"abcde"
        assert log.bytes_written == 5
        assert log.write(b"more") is True
        assert log.write(b"") is False
        assert path.read_bytes() == b"abcde"
    finally:
        log.close()


def test_exactly_filling_cap_is_not_overflow(tmp_path: Path) -> None:
    log = CappedLog(tmp_path / "output.log", max_bytes=3)
    try:
        assert log.write(b"abc") is False
        assert log.write(b"d") is True
    finally:
        log.close()


def test_close_is_repeatable_and_closed_writer_rejects_writes(tmp_path: Path) -> None:
    log = CappedLog(tmp_path / "output.log")
    log.close()
    log.close()
    with pytest.raises(ShellError):
        log.write(b"data")


def test_writer_does_not_overwrite_existing_log(tmp_path: Path) -> None:
    path = tmp_path / "output.log"
    path.write_bytes(b"keep")
    with pytest.raises(ShellError):
        CappedLog(path)
    assert path.read_bytes() == b"keep"


@pytest.mark.parametrize("finished", [False, True])
def test_empty_log_is_at_current_eof(tmp_path: Path, finished: bool) -> None:
    path = tmp_path / "output.log"
    path.touch()
    page = read_page(path, offset=0, finished=finished)
    assert page.output == ""
    assert page.next_offset == 0
    assert page.eof is True
    assert page.has_more is False


def test_reads_from_byte_offset_without_a_shared_cursor(tmp_path: Path) -> None:
    path = tmp_path / "output.log"
    path.write_bytes(b"one\ntwo\n")
    page = read_page(path, offset=4, finished=True)
    again = read_page(path, offset=4, finished=True)
    assert page.output == "two\n"
    assert page.next_offset == 8
    assert page.eof is True
    assert again == page


def test_line_limit_returns_exact_continuation(tmp_path: Path) -> None:
    path = tmp_path / "output.log"
    path.write_bytes(b"x\n" * 2001)
    first = read_page(path, offset=0, finished=True)
    second = read_page(path, offset=first.next_offset, finished=True)
    assert first.output == "x\n" * 2000
    assert first.next_offset == 4000
    assert first.has_more is True
    assert first.eof is False
    assert second.output == "x\n"
    assert second.next_offset == 4002
    assert second.eof is True


def test_long_line_is_split_without_skipping_bytes(tmp_path: Path) -> None:
    path = tmp_path / "output.log"
    path.write_bytes(b"x" * 51205)
    first = read_page(path, offset=0, finished=True)
    second = read_page(path, offset=first.next_offset, finished=True)
    assert first.output == "x" * 51200
    assert first.next_offset == 51200
    assert first.has_more is True
    assert second.output == "xxxxx"
    assert second.next_offset == 51205
    assert second.eof is True


def test_page_boundary_does_not_split_valid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "output.log"
    path.write_bytes(b"x" * 51199 + "😀!".encode())
    first = read_page(path, offset=0, finished=True)
    second = read_page(path, offset=first.next_offset, finished=True)
    assert first.output == "x" * 51199
    assert first.next_offset == 51199
    assert second.output == "😀!"
    assert second.next_offset == 51204


def test_running_partial_character_is_retried_after_append(tmp_path: Path) -> None:
    path = tmp_path / "output.log"
    path.write_bytes(b"hello \xe2\x82")
    first = read_page(path, offset=0, finished=False)
    assert first.output == "hello "
    assert first.next_offset == 6
    assert first.eof is True
    assert first.has_more is False
    with path.open("ab") as stream:
        stream.write(b"\xac")
    second = read_page(path, offset=first.next_offset, finished=False)
    assert second.output == "€"
    assert second.next_offset == 9


def test_finished_partial_character_is_replaced_and_consumed(tmp_path: Path) -> None:
    path = tmp_path / "output.log"
    path.write_bytes(b"hello \xe2\x82")
    page = read_page(path, offset=0, finished=True)
    assert page.output == "hello �"
    assert page.next_offset == 8
    assert page.eof is True


def test_invalid_bytes_respect_rendered_limit_and_source_offsets(tmp_path: Path) -> None:
    path = tmp_path / "output.log"
    path.write_bytes(b"\xff" * 18000)
    first = read_page(path, offset=0, finished=True)
    second = read_page(path, offset=first.next_offset, finished=True)
    assert first.output == "�" * 17066
    assert first.next_offset == 17066
    assert len(first.output.encode()) == 51198
    assert first.output + second.output == "�" * 18000
    assert second.next_offset == 18000


def test_replacement_group_is_not_partially_consumed(tmp_path: Path) -> None:
    path = tmp_path / "output.log"
    path.write_bytes(b"x" * 51197 + b"\xe2A")
    first = read_page(path, offset=0, finished=True)
    second = read_page(path, offset=first.next_offset, finished=True)
    assert first.output == "x" * 51197
    assert first.next_offset == 51197
    assert second.output == "�A"
    assert second.next_offset == 51199


@pytest.mark.parametrize("offset", [2, 3])
def test_rejects_offset_inside_valid_character(tmp_path: Path, offset: int) -> None:
    path = tmp_path / "output.log"
    path.write_bytes("A€B".encode())
    with pytest.raises(ShellError):
        read_page(path, offset=offset, finished=True)


def test_offset_can_start_at_character_or_at_invalid_byte(tmp_path: Path) -> None:
    path = tmp_path / "output.log"
    path.write_bytes("A€B".encode())
    assert read_page(path, offset=1, finished=True).output == "€B"
    path.write_bytes(b"\xff\x80A")
    assert read_page(path, offset=1, finished=True).output == "�A"


@pytest.mark.parametrize("offset", [-1, 4])
def test_rejects_offset_outside_log(tmp_path: Path, offset: int) -> None:
    path = tmp_path / "output.log"
    path.write_bytes(b"abc")
    with pytest.raises(ShellError):
        read_page(path, offset=offset, finished=True)


def test_exact_eof_offset_returns_empty_page(tmp_path: Path) -> None:
    path = tmp_path / "output.log"
    path.write_bytes(b"abc")
    page = read_page(path, offset=3, finished=True)
    assert page.output == ""
    assert page.next_offset == 3
    assert page.eof is True


def test_missing_log_returns_tool_error(tmp_path: Path) -> None:
    with pytest.raises(ShellError):
        read_page(tmp_path / "missing", offset=0, finished=False)


def test_invalid_lead_does_not_consume_next_character_prefix(tmp_path: Path) -> None:
    path = tmp_path / "output.log"
    path.write_bytes(b"x" * 51197 + b"\xe2\xc2\xa2")
    first = read_page(path, offset=0, finished=True)
    second = read_page(path, offset=first.next_offset, finished=True)
    assert first.output == "x" * 51197 + "�"
    assert first.next_offset == 51198
    assert second.output == "¢"
    assert second.next_offset == 51200


@pytest.mark.parametrize(
    "raw",
    [
        ("Привет 😀\r\n" * 7000).encode(),
        (b"\xe2A\xff\xc2\xa2\r\n" * 7000) + b"\xe2\x82",
    ],
)
def test_paginated_read_matches_complete_text_without_data_loss(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "output.log"
    path.write_bytes(raw)
    pages: list[str] = []
    offset = 0
    for _ in range(100):
        page = read_page(path, offset=offset, finished=True)
        assert len(page.output.encode()) <= 51200
        assert len(page.output.splitlines()) <= 2000
        pages.append(page.output)
        if page.eof:
            assert page.next_offset == len(raw)
            break
        assert offset < page.next_offset <= offset + 51200
        offset = page.next_offset
    else:
        pytest.fail("Pagination made no bounded progress")
    assert "".join(pages) == raw.decode("utf-8", errors="replace")
