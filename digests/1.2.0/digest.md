# Дайджест релиза analyzer-agent-service 1.2.0 (2026-07-23)

> Источник: `C:\dev\analyzer-agent-service`, диапазон `v1.1.1..v1.2.0`.
> Разведывательный обзор родительского проекта. LUNA — независимый проект:
> ничего отсюда не переносится автоматически, это материал для решения.

## TL;DR
- Первый крупный релиз *после* развилки с LUNA (публичный LUNA 0.1.0 — тот же день). Дальше родитель и LUNA расходятся всерьёз.
- Сессии изолируются Linux-пользователем + nftables; агенту дают `bash`. Это стендовый Linux, не Windows-dev LUNA.
- Стрим сменили SSE → WebSocket; появился `ToolOutputTruncationMiddleware`.
- Домен: vision/3D (cadquery, trimesh), KAG, реестр моделей GPUStack.

## Нововведения (по продукту)
### Песочница сессии
- **Что:** отдельный Linux-пользователь на сессию, whitelist исходящей сети через nftables, тул `bash` внутри зоны.
- **Зачем:** агент может исполнять код, не вылезая из клетки и не ходя куда не надо.

### Усечение выводов тулов
- **Что:** слишком большой результат тула режется (лимиты порядка 50 КБ / 2000 строк); полная копия кладётся в скрытый файл, модель может дочитать.
- **Зачем:** `read`/`grep`/поиск больше не взрывают контекст.

### Vision и 3D
- **Что:** реестр моделей, vision-субагент, разбор моделей через cadquery/trimesh, ресайз картинок.
- **Зачем:** смотреть чертежи и 3D, не запихивая сырой файл в текстовый контекст.

### Стрим по WebSocket
- **Что:** SSE заменён на WS.
- **Зачем:** двусторонний канал (в т.ч. под sandbox/HITL на стенде). На фронте — карточка субагента и токен-бар.

## Техническая реализация
### Truncation
- **Как сделано:** `ToolOutputTruncationMiddleware` — inner в стеке, `awrap_tool_call`. Стратегия head/tail; в 1.2.0 ещё есть поимённый whitelist «head»-тулов (в 1.2.4 его уберут). Полный вывод → `workspace/.truncated_outputs/`.
- **Ключевые файлы:** `core/agent/middleware/truncation.py` (+189), `core/agent/tools/truncation_utils.py` (+215).
- **Заметки:** паттерн общий, не доменный. В LUNA такого middleware нет.

### Sandbox
- **Как сделано:** `user_manager` создаёт системного пользователя, `nft_manager` вешает правила, `SandboxExec` гоняет команды. Деплой просит `NET_ADMIN`. Каталог `/app/` скрыт от per-session uid.
- **Ключевые файлы:** `core/agent/sandbox/{user_manager,nft_manager,exec}.py`, `core/agent/tools/bash_tool.py` (+78).
- **Заметки:** жёстко Linux. LUNA — Windows-first; nftables/uid на хосте разработчика не встанут.

### Транспорт
- **Как сделано:** `core/app/sse.py` удалён (−159), вместо него `core/app/ws.py` (+120). Файловые тулы переименованы в `read`/`write`/`edit`/`ls`/`grep`/`find`/`rm`, `.runtime` закрыт.
- **Ключевые файлы:** `core/app/ws.py`, `core/agent/tools/files_tools.py` (+473/-62).

### Прочее ядро
- **Как сделано:** процедурная память копируется в директорию сессии; MLflow — `prompts_enabled`, `prompt_cache_ttl`, сидинг; `core/logging_setup.py`; `models.json` + `ModelsRegistry`.
- **Ключевые файлы:** `core/agent/observability.py` (+110), `core/models_registry.py` (+155), `core/logging_setup.py` (+51).

## Кандидаты на перенос в LUNA
| Идея | Ценность для LUNA | Сложность | Совместимо с LUNA | Заметка |
|------|-------------------|-----------|-----------------|---------|
| `ToolOutputTruncationMiddleware` | высокая | средняя | да | Safety net на любой тул; полный вывод на диск. Не тащить поимённый whitelist — сразу декларацию с 1.2.4. |
| Короткие имена файловых тулов (`ls`/`grep`/`find`) | средняя | низкая | да | LUNA всё ещё `read_file`/`list_files`/`search_files`. Переименование ломает привычку модели и тесты. |
| Карточка субагента в ленте | средняя | средняя | да | В LUNA UI субагент никак не выделен. |
| Токен-бар | низкая | низкая | да | Косметика, если появится учёт токенов в стриме. |
| Кэш/сидинг промптов MLflow | низкая | низкая | частично | LUNA уже ходит в Prompt Registry; кэш TTL — мелочь. |
| `bash` без Linux-песочницы | средняя | высокая | частично | Как subprocess в workspace — интересно; как uid+nftables — нет. Windows-first сильно усложняет. |

## Что НЕ тянем
- **Per-session Linux user + nftables + NET_ADMIN** — модель деплоя родителя; LUNA разрабатывается на Windows и не поднимает системных пользователей.
- **Замена SSE на WebSocket** — LUNA сознательно на SSE. Менять транспорт ради паритета незачем.
- **Vision-агент, cadquery, trimesh, KAG, CAIT API, `_ext` коллекции dim=4096** — домен и стендовая инфра.
- **nginx в compose / k8s prompt-seed-job** — деплой родителя.

## Приложение
- Теги: `v1.1.1..v1.2.0` · дата релиза: 2026-07-23
- Diffstat: 94 files changed, 6669 insertions(+), 2102 deletions(-)
