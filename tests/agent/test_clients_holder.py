"""
Тесты холдера процессных клиентов.

Холдер — мутабельный модульный синглтон. Чтобы он не протекал между тестами,
autouse-fixture сбрасывает `_clients` в None до и после каждого теста.

Сети/LLM здесь нет: `from_config` собирает клиентов голыми конструкторами (httpx ленив,
поднимается только при первом запросе), поэтому проверяем проводку config → клиент по
атрибутам, не делая ни одного реального вызова.
"""

from __future__ import annotations

import pytest
from langchain_core.tools import ToolException

import core.agent.tools._clients as holder
from core.agent.tools._clients import Clients, from_config, get_clients, set_clients
from core.config import InfraConfig
from core.services.rag_service import RAGService


@pytest.fixture(autouse=True)
def reset_holder():
    """Изолирует процессный синглтон: сброс до и после каждого теста."""
    holder._clients = None
    yield
    holder._clients = None


# --- get/set ------------------------------------------------------------------


def test_get_before_set_raises_toolexception():
    """Неинициализированный холдер → доменная ToolException (не TypeError/None)."""
    with pytest.raises(ToolException):
        get_clients()


def test_error_message_points_to_set_clients():
    """Текст ошибки указывает на причину (кривая проводка) — для self-correction."""
    with pytest.raises(ToolException, match="set_clients"):
        get_clients()


def test_set_then_get_returns_same_object():
    # типы не валидируются в рантайме (frozen dataclass) — достаточно маркеров.
    fake = Clients(rag=object())  # type: ignore[arg-type]
    set_clients(fake)
    assert get_clients() is fake


def test_set_overwrites_previous():
    first = Clients(rag=object())   # type: ignore[arg-type]
    second = Clients(rag=object())  # type: ignore[arg-type]
    set_clients(first)
    set_clients(second)
    assert get_clients() is second


def test_reset_fixture_isolates_between_tests():
    """Холдер пуст на входе в тест — значит предыдущий set не протёк."""
    with pytest.raises(ToolException):
        get_clients()
    set_clients(Clients(rag=object()))  # type: ignore[arg-type]


# --- from_config --------------------------------------------------------------


def test_from_config_builds_rag_service(monkeypatch):
    """RAG включён флагом → from_config строит RAGService."""
    monkeypatch.setenv("RAG_ENABLED", "true")
    cfg = InfraConfig()
    clients = from_config(cfg)

    assert isinstance(clients.rag, RAGService)


def test_from_config_rag_disabled_returns_empty_holder():
    """RAG выключен (дефолт) → клиенты не строятся, rag=None (проводка сохранена)."""
    cfg = InfraConfig()
    clients = from_config(cfg)

    assert clients.rag is None


def test_from_config_result_is_usable_via_holder():
    """from_config → set_clients → get_clients отдаёт тот же объект (сквозной путь)."""
    cfg = InfraConfig()
    clients = from_config(cfg)
    set_clients(clients)
    assert get_clients() is clients
