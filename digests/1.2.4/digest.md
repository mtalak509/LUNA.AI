# Дайджест релиза analyzer-agent-service 1.2.4 (2026-08-06)

> Источник: `C:\dev\analyzer-agent-service`, диапазон `v1.2.3..v1.2.4`.
> Разведывательный обзор родительского проекта. LUNA — независимый проект:
> ничего отсюда не переносится автоматически, это материал для решения.

## TL;DR
- Опциональный shared-password на агент-сервере: HMAC-токен в Bearer / cookie / `?token=` (для WS). Пустой пароль — auth выключен.
- Стратегия truncation больше не угадывается по имени тула: `@agent_tool(truncation="head"|"tail")`.
- Починен префикс путей вложений в working context (`workspace/...`), иначе файловые тулы не резолвили аттачи.
- Для LUNA интересны auth (личный HTTP на чужой машине) и декларативный truncation — если брать middleware из 1.2.0.

## Нововведения (по продукту)
### Shared-password auth
- **Что:** если задан `AUTH_PASSWORD` — страница `login.html`, `/auth/login`/`logout`, вход по паролю, TTL токена. Иначе middleware — no-op.
- **Зачем:** закрыть дев/стендовый UI простой дверью, не поднимая IdP.

## Техническая реализация
### Auth
- **Как сделано:** `itsdangerous.URLSafeTimedSerializer`; секрет либо `AUTH_SECRET`, либо SHA-256 от пароля. Сравнение пароля через `compare_digest` по хешам. Публичные `/health`, `/login`, `/auth/*`.
- **Ключевые файлы:** `core/app/auth.py` (+202), `core/app/routes/auth.py` (+60), `devfront/static/login.html` (+129), `core/config.py` (+11).
- **Заметки:** в 1.3.0 эту глобальную дверь снимут в пользу admin-only на отдельных профилях. Для LUNA вариант 1.2.4 (опциональный gate на весь сервер) ближе: один пользователь, один процесс.

### Truncation по декларации
- **Как сделано:** атрибут в `tool.metadata`; middleware читает его, поимённый whitelist удалён. Иначе новый search-тул молча получал `tail` и терял начало.
- **Ключевые файлы:** `core/agent/middleware/truncation.py` (+8/-34), `core/agent/tools/__init__.py` (+10), правки metadata на тулах.
- **Заметки:** если переносить truncation в LUNA — сразу этот контракт, не whitelist 1.2.0.

### Attachments paths
- **Как сделано:** `AttachmentsContextProvider` отдаёт пути с префиксом `workspace/`.
- **Ключевые файлы:** `core/agent/middleware/context_inject.py` (+4/-1).
- **Заметки:** стоит сверить, как LUNA печатает пути аттачей сейчас.

## Кандидаты на перенос в LUNA
| Идея | Ценность для LUNA | Сложность | Совместимо с LUNA | Заметка |
|------|-------------------|-----------|-----------------|---------|
| Опциональный shared-password на HTTP | высокая | средняя | да | LUNA сейчас без auth. Для `luna-web` в LAN — самая дешёвая дверь. Брать 1.2.4 (выключено, если пароль пуст), не breaking 1.3.0. |
| `@agent_tool(truncation=...)` | высокая | низкая | да | Имеет смысл только вместе с middleware из 1.2.0. |
| Префикс `workspace/` у аттачей в контексте | низкая | низкая | да | Проверить текущий LUNA, не тащить вслепую. |

## Что НЕ тянем
- **`?token=` под WebSocket** — в LUNA стрим SSE; cookie/Bearer достаточно.
- **Глобальный обязательный admin-пароль из 1.3.0** — см. следующий дайджест; для LUNA хуже опционального gate.

## Приложение
- Теги: `v1.2.3..v1.2.4` · дата релиза: 2026-08-06
- Diffstat: 30 files changed, 771 insertions(+), 225 deletions(-)
