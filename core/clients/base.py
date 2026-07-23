"""
BaseHTTPClient — общий транспорт для сырых HTTP-клиентов (embedding-endpoint и любые внешние API).

Конкретные клиенты наследуются и описывают только свои эндпоинты через `_get`/`_post`.

Слой нейтрален к LangChain/LangGraph.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.clients.errors import (
    UpstreamAuthError,
    UpstreamBadResponse,
    UpstreamConnectionError,
    UpstreamNotFound,
    UpstreamServerError,
    UpstreamTimeout,
    UpstreamTransient,
)

logger = logging.getLogger(__name__)


class BaseHTTPClient:
    """Базовый async HTTP-клиент. Наследники добавляют методы поверх `_get`/`_post`.

    Использование:
        async with MyAPIClient(base_url=..., service="my-api", ...) as client:
            data = await client.get_items()

    Либо как долгоживущий объект (оркестратор / контейнер-сессия): создать, на старте
    приложения вызвать `await client.__aenter__()` (или дождаться первого запроса —
    клиент поднимется лениво), на остановке — `await client.aclose()`.
    """

    def __init__(
        self,
        base_url: str,
        *,
        service: str,
        timeout_s: float = 30.0,
        retries: int = 3,
        backoff_base_s: float = 0.5,
        headers: dict[str, str] | None = None,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.service = service
        self.timeout_s = timeout_s
        self.retries = retries
        self.backoff_base_s = backoff_base_s

        self._headers = dict(headers or {})
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

        # transport — шов для тестов (httpx.MockTransport); в проде None (реальная сеть).
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    # --- lifecycle ----------------------------------------------------------

    def _ensure_client(self) -> httpx.AsyncClient:
        """Лениво поднять AsyncClient в текущем event loop."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._headers,
                timeout=self.timeout_s,
                transport=self._transport,
            )
        return self._client

    async def __aenter__(self) -> BaseHTTPClient:
        self._ensure_client()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # --- core request -------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        """Выполнить запрос с ретраями и вернуть распарсенный JSON.

        Ретраятся только `UpstreamTransient` (timeout / обрыв / 5xx). 4xx
        (auth / not-found / bad) поднимаются сразу. JSON парсится один раз
        после успешного ответа.
        """
        response: httpx.Response | None = None
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type(UpstreamTransient),
            stop=stop_after_attempt(self.retries),
            wait=wait_exponential(multiplier=self.backoff_base_s),
            before_sleep=before_sleep_log(logger, logging.DEBUG),
            reraise=True,
        ):
            with attempt:
                response = await self._send(method, path, params=params, json=json)

        return self._parse_json(response)  # type: ignore[arg-type]

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        json: Any | None,
    ) -> httpx.Response:
        """Один сетевой вызов + маппинг ошибок транспорта/статуса в таксономию."""
        client = self._ensure_client()
        logger.debug("[%s] -> %s %s params=%s", self.service, method, path, params)
        try:
            response = await client.request(method, path, params=params, json=json)
        except httpx.TimeoutException as e:
            raise UpstreamTimeout(
                f"timeout on {method} {path}", url=path, service=self.service
            ) from e
        except httpx.TransportError as e:  # ConnectError и пр. транспортные
            raise UpstreamConnectionError(
                f"connection error on {method} {path}: {e}",
                url=path,
                service=self.service,
            ) from e

        logger.debug(
            "[%s] <- %s %s (%s)", self.service, method, response.url, response.status_code
        )
        self._raise_for_status(response)
        return response

    # --- helpers ------------------------------------------------------------

    def _raise_for_status(self, response: httpx.Response) -> None:
        """HTTP-статус → наша таксономия. 5xx транзиентна, 4xx — нет."""
        code = response.status_code
        if code < 400:
            return

        ctx = {"status_code": code, "url": str(response.url), "service": self.service}
        if code in (401, 403):
            raise UpstreamAuthError(f"auth failed ({code})", **ctx)
        if code == 404:
            raise UpstreamNotFound(f"not found ({code})", **ctx)
        if code >= 500:
            raise UpstreamServerError(f"server error ({code})", **ctx)
        raise UpstreamBadResponse(f"bad response ({code}): {response.text[:200]}", **ctx)

    def _parse_json(self, response: httpx.Response) -> Any:
        """Распарсить тело как JSON; не-JSON → UpstreamBadResponse (не ретраится)."""
        try:
            return response.json()
        except ValueError as e:
            raise UpstreamBadResponse(
                f"non-JSON response: {response.text[:200]}",
                status_code=response.status_code,
                url=str(response.url),
                service=self.service,
            ) from e

    # --- thin verb wrappers (для читаемости в наследниках) ------------------

    async def _get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def _post(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        return await self._request("POST", path, params=params, json=json)
