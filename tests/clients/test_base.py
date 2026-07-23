"""
Unit-тесты BaseHTTPClient: ретрай-политика, маппинг статусов в таксономию, auth.

Без сети. План предполагал `respx`, но он недоступен оффлайн — используем встроенный
`httpx.MockTransport` (тот же механизм подмены транспорта, что и у respx). Транспорт
прокидывается в клиент через параметр `transport=` конструктора.
"""

from __future__ import annotations

import httpx
import pytest

from core.clients.base import BaseHTTPClient
from core.clients.errors import (
    UpstreamAuthError,
    UpstreamBadResponse,
    UpstreamConnectionError,
    UpstreamNotFound,
    UpstreamServerError,
    UpstreamTimeout,
)


def make_client(handler, **kwargs) -> BaseHTTPClient:
    """Клиент поверх MockTransport. backoff=0 — чтобы ретраи не тормозили тест."""
    transport = httpx.MockTransport(handler)
    params = dict(
        base_url="http://test",
        service="test",
        retries=3,
        backoff_base_s=0.0,
        transport=transport,
    )
    params.update(kwargs)
    return BaseHTTPClient(**params)


class CallCounter:
    """Обёртка-handler, считающая число вызовов транспорта (для проверки ретраев)."""

    def __init__(self, fn):
        self.fn = fn
        self.count = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.count += 1
        return self.fn(request)


# --- успешный путь ----------------------------------------------------------

async def test_get_returns_parsed_json():
    client = make_client(lambda req: httpx.Response(200, json={"ok": True}))
    async with client:
        data = await client._get("/ping")
    assert data == {"ok": True}


async def test_request_builds_url_and_params():
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        return httpx.Response(200, json={})

    async with make_client(handler) as client:
        await client._get("/search", params={"q": "болт", "n": 5})

    assert seen["url"] == "http://test/search?q=%D0%B1%D0%BE%D0%BB%D1%82&n=5"


async def test_post_sends_json_body():
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = req.content
        return httpx.Response(200, json={})

    async with make_client(handler) as client:
        await client._post("/calibers", json={"a": 1})

    assert b'"a"' in seen["body"]


async def test_auth_header_injected():
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers.get("Authorization")
        return httpx.Response(200, json={})

    async with make_client(handler, api_key="secret") as client:
        await client._get("/x")

    assert seen["auth"] == "Bearer secret"


# --- маппинг статусов в таксономию ------------------------------------------

@pytest.mark.parametrize(
    "status,exc",
    [
        (401, UpstreamAuthError),
        (403, UpstreamAuthError),
        (404, UpstreamNotFound),
        (400, UpstreamBadResponse),
        (422, UpstreamBadResponse),
    ],
)
async def test_4xx_mapped_and_not_retried(status, exc):
    counter = CallCounter(lambda req: httpx.Response(status, json={}))
    async with make_client(counter) as client:
        with pytest.raises(exc) as ei:
            await client._get("/x")
    assert ei.value.status_code == status
    assert counter.count == 1  # 4xx не ретраится


async def test_non_json_body_is_bad_response():
    async with make_client(lambda req: httpx.Response(200, text="<html>oops")) as client:
        with pytest.raises(UpstreamBadResponse):
            await client._get("/x")


# --- ретраи (транзиентные ошибки) -------------------------------------------

async def test_5xx_retried_then_raised():
    counter = CallCounter(lambda req: httpx.Response(500, json={}))
    async with make_client(counter) as client:
        with pytest.raises(UpstreamServerError):
            await client._get("/x")
    assert counter.count == 3  # retries=3 → 3 попытки


async def test_timeout_retried_then_raised():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=req)

    counter = CallCounter(handler)
    async with make_client(counter) as client:
        with pytest.raises(UpstreamTimeout):
            await client._get("/x")
    assert counter.count == 3


async def test_connection_error_retried_then_raised():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=req)

    counter = CallCounter(handler)
    async with make_client(counter) as client:
        with pytest.raises(UpstreamConnectionError):
            await client._get("/x")
    assert counter.count == 3


async def test_transient_then_success():
    """5xx на первой попытке, 200 на второй — клиент возвращает результат."""

    def handler(req: httpx.Request) -> httpx.Response:
        if handler.calls == 0:
            handler.calls += 1
            return httpx.Response(503, json={})
        return httpx.Response(200, json={"ok": 1})

    handler.calls = 0
    counter = CallCounter(handler)
    async with make_client(counter) as client:
        data = await client._get("/x")
    assert data == {"ok": 1}
    assert counter.count == 2
