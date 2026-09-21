# Дайджест релиза analyzer-agent-service 1.2.2 (2026-07-29)

> Источник: `C:\dev\analyzer-agent-service`, диапазон `v1.2.1..v1.2.2`.
> Разведывательный обзор родительского проекта. LUNA — независимый проект:
> ничего отсюда не переносится автоматически, это материал для решения.

## TL;DR
- Компакция срабатывает на 80% `context_window` модели, а не на захардкоженных 70k токенов — прямой кандидат для LUNA.
- Появился guidebook/tutorial: агенту кладут навигатор «прочитай гайд, потом действуй».
- High-level `tp_edit` и CAIT-поиск — домен ТП, не наш слой.
- `recursion_limit` поднят с 50 до 1000; в LUNA до сих пор 50.

## Нововведения (по продукту)
### Tutorial / guidebook
- **Что:** `guidebook/tutorial/` — intro, техпроцесс, тулы, сценарии, CAIT, вложения. Каталог `guide` переименован в `guidebook`.
- **Зачем:** пользовательские возможности описаны так, чтобы их читал и человек, и агент.

### High-level правка ТП
- **Что:** набор `tp_add_*` / `tp_update_node` / `tp_delete_node` / `tp_init_workpiece` поверх `tp_patch`.
- **Зачем:** модель меньше собирает jsonpatch вручную и меньше падает на list-родителях.

## Техническая реализация
### Компакция от окна модели
- **Как сделано:** `BaseAgent` берёт `context_window` из конфига модели (fallback 128k) и ставит `trigger_tokens = int(ctx * 0.8)` в `HistoryCompactor`.
- **Ключевые файлы:** `core/agent/base.py` (+7/-2).
- **Заметки:** в LUNA `HistoryCompactor` по-прежнему с `trigger_tokens: int = 70_000`. Перенос — несколько строк плюс откуда брать окно (у нас нет `models.json`).

### Guidebook
- **Как сделано:** markdown в `core/procedures/guidebook/` + ужатый `main_agent.ru.md`, который отсылает читать гайды. Появились skills (`caliber-selection` со скриптами).
- **Ключевые файлы:** `core/procedures/guidebook/**`, `core/procedures/main_agent.ru.md` (+74/-209).
- **Заметки:** идея «процедурная память = файлы, агент сам читает» — filesystem-first. Содержание гайдов — заводское.

### Прочее
- **Как сделано:** `DEFAULT_RECURSION_LIMIT = 1000`; whitelist DNS kube-dns в sandbox; vision-каталог переименован под `subagent_type`.
- **Ключевые файлы:** `core/agent/base.py`, `core/agent/tools/techprocess_edit_tools.py` (+918).

## Кандидаты на перенос в LUNA
| Идея | Ценность для LUNA | Сложность | Совместимо с LUNA | Заметка |
|------|-------------------|-----------|-----------------|---------|
| Компакция = 80% окна модели | высокая | низкая | да | Окно можно взять из конфига провайдера / env, не обязательно `models.json`. |
| Guidebook как файлы, которые агент читает сам | средняя | средняя | да | Имеет смысл *пустой* каркас (как пользоваться json_*, файлами, HITL), без CAIT/ТП. |
| `recursion_limit` 1000 | низкая | низкая | да | LUNA = 50. Поднять стоит, если длинные tool-loops реально упираются. |

## Что НЕ тянем
- **`tp_edit` / `tp_init_workpiece` и фиксы list-родителей ТП** — схема техпроцесса.
- **CAIT search-тулы и skill подбора калибров** — домен и внешнее API.
- **kube-dns в nftables whitelist** — нет sandbox.

## Приложение
- Теги: `v1.2.1..v1.2.2` · дата релиза: 2026-07-29
- Diffstat: 40 files changed, 5024 insertions(+), 216 deletions(-)
