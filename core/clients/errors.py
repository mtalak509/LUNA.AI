from __future__ import annotations


class UpstreamError(Exception):
    """Базовая ошибка обращения к внешнему сервису.

      Несёт контекст для логов/сообщений агенту, чтобы верх не угадывал его из текста.
      """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        url: str | None = None,
        service: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.url = url
        self.service = service

    def __str__(self) -> str:
        parts = [self.message]
        if self.status_code:
            parts.append(f"status={self.status_code}")
        if self.service:
            parts.append(f"service={self.service}")
        if self.url:
            parts.append(f"url={self.url}")
        return " | ".join(parts)


    # --- Транзиентные: имеет смысл ретраить -------------------------------------

class UpstreamTransient(UpstreamError):
    """Временная ошибка — кандидат на повтор (ретраит BaseHTTPClient)."""


class UpstreamTimeout(UpstreamTransient):
    """Истёк таймаут запроса (httpx.TimeoutException)."""


class UpstreamConnectionError(UpstreamTransient):
    """Не удалось установить соединение / обрыв транспорта (httpx.TransportError)."""


class UpstreamServerError(UpstreamTransient):
    """5xx от апстрима — на стороне сервера, повтор оправдан."""


# --- Перманентные: ретрай бессмыслен ----------------------------------------

class UpstreamAuthError(UpstreamError):
    """401 / 403 — проблема авторизации, повтор не поможет."""


class UpstreamNotFound(UpstreamError):
    """404 — ресурса нет."""


class UpstreamBadResponse(UpstreamError):
    """Прочие 4xx, либо тело не парсится в ожидаемый JSON."""
