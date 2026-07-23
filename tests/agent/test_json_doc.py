"""
Unit-тесты хелперов JSON-документа (core/agent/tools/json_doc.py).

Функции чистые (без рантайма агента) — тестируются напрямую на temp-каталогах и
in-memory структурах. Покрытие: load/save round-trip + атомарность, get_node (RFC 6901),
apply_ops (RFC 6902, транзакционность), render_level (карта одного уровня), lexical_search
(лексический поиск). Проверяем в т.ч. что при дефолтах НИЧЕГО не обрезается (капы=None).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.agent.tools import json_doc as jd
from core.agent.tools.json_doc import JsonError

_DOC = "doc.json"


def _sample() -> dict:
    """Свежая копия синтетического документа на каждый тест (apply_ops не должен мутировать)."""
    return {
        "part": {"id": "ABC-123", "material": "Сталь 45"},
        "route": [
            {"op": "Токарная", "machine": "16К20"},
            {"op": "Фрезерная", "machine": "6Р13"},
        ],
        "meta": {"version": 2, "approved": True, "note": None},
    }


# --- load_doc / save_doc ------------------------------------------------------------


def test_save_load_round_trip(tmp_path: Path) -> None:
    p = tmp_path / _DOC
    doc = _sample()
    jd.save_doc(p, doc)
    assert jd.load_doc(p) == doc


def test_save_pretty_and_cyrillic_not_escaped(tmp_path: Path) -> None:
    p = tmp_path / _DOC
    jd.save_doc(p, _sample())
    raw = p.read_text(encoding="utf-8")
    assert "Сталь 45" in raw  # ensure_ascii=False — кириллица не в \uXXXX
    assert '\n  "part"' in raw  # indent=2


def test_save_is_atomic_no_tmp_left(tmp_path: Path) -> None:
    p = tmp_path / _DOC
    jd.save_doc(p, _sample())
    assert not (tmp_path / (_DOC + ".tmp")).exists()


def test_save_overwrites_existing(tmp_path: Path) -> None:
    p = tmp_path / _DOC
    jd.save_doc(p, {"v": 1})
    jd.save_doc(p, {"v": 2})
    assert jd.load_doc(p) == {"v": 2}


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(JsonError):
        jd.load_doc(tmp_path / "nope.json")


def test_load_broken_json_raises(tmp_path: Path) -> None:
    p = tmp_path / _DOC
    p.write_text("{ это не json ", encoding="utf-8")
    with pytest.raises(JsonError):
        jd.load_doc(p)


# --- get_node (RFC 6901) ------------------------------------------------------------


def test_get_node_empty_pointer_returns_whole_doc() -> None:
    doc = _sample()
    assert jd.get_node(doc, "") is doc


def test_get_node_nested_and_array_index() -> None:
    doc = _sample()
    assert jd.get_node(doc, "/part/material") == "Сталь 45"
    assert jd.get_node(doc, "/route/0/op") == "Токарная"


def test_get_node_escaped_key() -> None:
    doc = {"a/b": {"x": 1}}
    assert jd.get_node(doc, "/a~1b/x") == 1


def test_get_node_miss_raises() -> None:
    with pytest.raises(JsonError):
        jd.get_node(_sample(), "/route/99")


def test_get_node_malformed_pointer_raises() -> None:
    # Указатель без ведущего '/' невалиден по RFC 6901 → JsonError, ход не падает.
    with pytest.raises(JsonError):
        jd.get_node(_sample(), "part")


# --- apply_ops (RFC 6902) -----------------------------------------------------------


def test_apply_ops_replace_returns_new_doc() -> None:
    doc = _sample()
    out = jd.apply_ops(doc, [{"op": "replace", "path": "/part/material", "value": "Латунь"}])
    assert out["part"]["material"] == "Латунь"


def test_apply_ops_does_not_mutate_original() -> None:
    doc = _sample()
    jd.apply_ops(doc, [{"op": "replace", "path": "/part/material", "value": "Латунь"}])
    assert doc["part"]["material"] == "Сталь 45"  # оригинал цел (in_place=False)


def test_apply_ops_add_append_and_remove() -> None:
    doc = _sample()
    out = jd.apply_ops(doc, [{"op": "add", "path": "/route/-", "value": {"op": "Шлифовальная"}}])
    assert len(out["route"]) == 3 and out["route"][2] == {"op": "Шлифовальная"}
    out2 = jd.apply_ops(doc, [{"op": "remove", "path": "/meta/note"}])
    assert "note" not in out2["meta"]


def test_apply_ops_copy_op() -> None:
    # Копирование узла — через нативный RFC 6902 copy.
    doc = _sample()
    out = jd.apply_ops(doc, [{"op": "copy", "from": "/route/0", "path": "/route/-"}])
    assert len(out["route"]) == 3
    assert out["route"][2] == out["route"][0]


def test_apply_ops_empty_list_raises() -> None:
    with pytest.raises(JsonError):
        jd.apply_ops(_sample(), [])


def test_apply_ops_bad_path_raises_and_keeps_original() -> None:
    doc = _sample()
    with pytest.raises(JsonError):
        jd.apply_ops(doc, [{"op": "replace", "path": "/missing/deep", "value": 1}])
    assert doc == _sample()  # фейл патча не оставил следов


def test_apply_ops_invalid_operation_raises() -> None:
    with pytest.raises(JsonError):
        jd.apply_ops(_sample(), [{"op": "bogus", "path": "/part", "value": 1}])


# --- render_level (карта одного уровня) ---------------------------------------------


def test_render_level_dict_collapses_branches() -> None:
    out = jd.render_level(_sample())
    assert '"part": object: 2 ключей' in out
    assert '"route": array: 2 элементов' in out
    assert '"meta": object: 3 ключей' in out


def test_render_level_dict_shows_scalar_leaves() -> None:
    out = jd.render_level(_sample()["part"])
    assert out == '"id": "ABC-123"\n"material": "Сталь 45"'


def test_render_level_array_indices() -> None:
    out = jd.render_level(_sample()["route"])
    assert out == "[0]: object: 2 ключей\n[1]: object: 2 ключей"


@pytest.mark.parametrize(
    "node,expected",
    [("Сталь 45", '"Сталь 45"'), (None, "null"), (True, "true"), (42, "42")],
)
def test_render_level_scalar(node, expected) -> None:
    assert jd.render_level(node) == expected


@pytest.mark.parametrize("node", [{}, []])
def test_render_level_empty(node) -> None:
    assert jd.render_level(node) == "(пусто)"


def test_render_level_max_children_caps() -> None:
    node = {"a": 1, "b": 2, "c": 3}
    out = jd.render_level(node, max_children=2)
    assert out.splitlines() == ['"a": 1', '"b": 2', "… ещё 1"]


def test_render_level_default_no_cap() -> None:
    node = list(range(50))
    out = jd.render_level(node)  # max_children=None → показываем всех, без хвоста
    assert len(out.splitlines()) == 50
    assert "ещё" not in out


# --- lexical_search -----------------------------------------------------------------


def test_search_value_match_case_insensitive() -> None:
    assert jd.lexical_search(_sample(), "сталь") == ['/part/material: "Сталь 45"']


def test_search_key_match_returns_value_snippet() -> None:
    assert jd.lexical_search(_sample(), "material") == ['/part/material: "Сталь 45"']


def test_search_inside_array() -> None:
    assert jd.lexical_search(_sample(), "Токарная") == ['/route/0/op: "Токарная"']


def test_search_path_hint_scopes() -> None:
    hits = jd.lexical_search(_sample(), "version", path_hint="/meta")
    assert hits == ["/meta/version: 2"]


def test_search_escapes_pointer_segments() -> None:
    doc = {"a/b": "значение"}
    assert jd.lexical_search(doc, "a/b") == ['/a~1b: "значение"']


def test_search_max_results_caps() -> None:
    doc = {"k1": "повтор", "k2": "повтор", "k3": "повтор"}
    assert len(jd.lexical_search(doc, "повтор", max_results=2)) == 2


def test_search_default_no_snippet_truncation() -> None:
    long_value = "x" * 500
    doc = {"field": long_value}
    hits = jd.lexical_search(doc, "xxx")
    assert hits == [f'/field: "{long_value}"']  # _SNIPPET_MAX_LEN=None → без обрезки


def test_search_no_match_returns_empty() -> None:
    assert jd.lexical_search(_sample(), "несуществующее") == []
