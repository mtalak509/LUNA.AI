# Дайджест релиза analyzer-agent-service 1.3.2 (2026-08-28)

> Источник: `C:\dev\analyzer-agent-service`, диапазон `v1.3.1..v1.3.2`.
> Разведывательный обзор родительского проекта. LUNA — независимый проект:
> ничего отсюда не переносится автоматически, это материал для решения.

## TL;DR
- Появился `graph_search`: read-only Cypher в отдельный сервис корпуса (Neo4j). Документ по-прежнему грузится из ЦАИТ.
- Воронка СТО схлопнута в один `sto_search` через Elasticsearch ЦАИТ; Qdrant `sto_classes` из потока убран.
- В sandbox-образ насыпали Excel/PDF/Word/plotly — имеет смысл только если в LUNA появится исполнение скриптов.
- Загрузка ТП больше не валидирует узлы схемой `Node` — только конверт. Это ближе к LUNA, но сам ресурс `techprocess` нам не нужен.

## Нововведения (по продукту)
### Графовый корпус ТП
- **Что:** тул `graph_search` (профили `admin` и `cait_techprocess_profile`) ходит в `analyzer-agent-graph`. Ответ — сводки и id; полный документ — из ЦАИТ. Рядом skill `graph-corpus` со схемой и рецептами Cypher.
- **Зачем:** искать похожие техпроцессы по графу, не сканируя JSON по одному.

### Остатки на складах
- **Что:** `api_get_rests(articuls)` — пакетная проверка наличия по артикулам ЦАИТ. Пустой артикул — штатный исход.
- **Зачем:** не предлагать материал/СТО, которого нет.

### Библиотеки для отчётов
- **Что:** в образ добавлены openpyxl/xlsxwriter/xlrd, pypdf/pdfplumber/weasyprint/python-docx, rapidfuzz/jmespath/networkx, plotly/seaborn/markdown. Для WeasyPrint — Cairo/Pango.
- **Зачем:** скрипты навыков и `bash` могут собрать отчёт без доустановки пакетов в поде.

## Техническая реализация
### GraphClient
- **Как сделано:** тонкий HTTP-клиент + тул, который шлёт Cypher и ловит ошибки сервиса. URL — `GRAPH_BASE_URL`. Skill копируется в профильные `procedures`.
- **Ключевые файлы:** `core/clients/graph_client.py` (+40), `core/agent/tools/graph_tools.py` (+53), `core/procedures/profiles/*/skills/graph-corpus/**`.
- **Заметки:** отдельный сервис + Neo4j. Для LUNA это и домен, и новая инфра.

### STO → Elasticsearch
- **Как сделано:** `sto_find_class` / `sto_resolve_class` / `sto_get_attributes` удалены. Один `sto_search(query, filters?, page?, size?)` параллельно бьёт `/sto/search/elastic` и `/elastic/filters`. Диапазоны считает ES, клиентский постфильтр снят.
- **Ключевые файлы:** `core/agent/tools/sto_tools.py` (+132/-200), `core/clients/cait_client.py` (+57/-55).

### Конверт ТП без схемы узлов
- **Как сделано:** `POST`/`PUT .../techprocess` проверяет `workpiece` (object|null) и `selectedRow` (array); `meta` без `_meta` переименовывается; узлы не гоняются через `Node`.
- **Ключевые файлы:** `core/agent/tools/techprocess_doc.py` (+21), `core/app/routes/workspace.py` (+31/-14).
- **Заметки:** философски это «документ как есть», как в LUNA. Но сам эндпоинт — про канонический ТП.

### nftables
- **Как сделано:** имя из whitelist, которое не резолвится в DNS, больше не попадает в правило сырой строкой.
- **Ключевые файлы:** `core/agent/sandbox/nft_manager.py` (+64/-26).

## Кандидаты на перенос в LUNA
| Идея | Ценность для LUNA | Сложность | Совместимо с LUNA | Заметка |
|------|-------------------|-----------|-----------------|---------|
| Не валидировать произвольный JSON схемой домена | уже есть | — | да | LUNA и так ест любой JSON по пути. Урок: не возвращать Pydantic-схему «главного» документа. |
| Набор библиотек для отчётов в runtime | низкая | низкая | частично | Имеет смысл пакетами extra, только если появится `run_script`/`bash`. Не тащить WeasyPrint+Cairo в базовый install. |
| Skill как «рецепты + схема внешнего API» | низкая | средняя | частично | Паттерн файлов навыка ок; Neo4j-рецепты — нет. |

## Что НЕ тянем
- **`graph_search` + Neo4j-корпус + `GRAPH_BASE_URL`** — отдельный сервис и домен ТП.
- **Elasticsearch-воронка СТО и `api_get_rests`** — API ЦАИТ.
- **Снятие клиентского постфильтра в пользу фасетов ES** — не из чего снимать.
- **Правки nftables whitelist** — нет sandbox.

## Приложение
- Теги: `v1.3.1..v1.3.2` · дата релиза: 2026-08-28
- Diffstat: 56 files changed, 2857 insertions(+), 11061 deletions(-)
