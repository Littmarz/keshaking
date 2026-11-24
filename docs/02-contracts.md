# Контракты между модулями

> Это **главный документ для параллельной работы**. Каждый пишет свой модуль под эти интерфейсы, а пока соседний модуль не готов, использует заглушку (mock).
> Изменения — только через PR с ревью **двух** человек (автор модуля-потребителя + капитан).
> ⛔ До 10:00 26.11 контракты существуют **только как описание полей** (таблицы ниже). Код `core/schemas.py` пишем **после старта спринта** по этим таблицам — см. [00-conditions.md](00-conditions.md).

Обозначения типов: `str?` — может быть пустым (None); `list[X]` — список; `date` — дата без времени; `datetime` — дата и время.

## 1. Данные (📄 разработчик 2 создаёт)

`DocStatus` — одно из: `active`, `superseded`, `cancelled`, `unknown`.

### Document

| Поле | Тип | Пример / смысл |
|---|---|---|
| doc_id | str | `reg-0042` |
| title | str | «Регламент согласования договоров» |
| doc_type | str | регламент / приказ / положение / продукт / … |
| number | str? | «№ 15-П» |
| version | str? | «ред. 3» |
| approved_at | date? | |
| effective_from | date? | |
| effective_to | date? | |
| status | DocStatus | вычисляется ingest-ом на дату `REFERENCE_DATE` (см. ниже) |
| status_reason | str? | «заменён приказом № 22 от 01.03.2026» |
| supersedes | list[str] | doc_id тех, кого заменяет (по умолчанию пусто) |
| superseded_by | str? | |
| access_level | str | `all` / `legal` / `sales` / `hr` / `management` (по умолчанию `all`) |
| source_path | str | `data/raw/reg-0042.docx` |

### Chunk

| Поле | Тип | Смысл |
|---|---|---|
| chunk_id | str | `reg-0042::3.2.1` |
| doc_id, doc_title | str | |
| section | str | номер пункта `3.2.1` — **обязателен** для цитат |
| section_title | str? | «Порядок подписания» |
| heading_path | list[str] | `["3. Подписание", "3.2 Агентские договоры"]` |
| text | str | чистый текст пункта (для цитаты) |
| page | int? | |
| status, version, effective_from, effective_to, access_level | — | **денормализовано** из Document — нужно для фильтров в Qdrant и для подписи цитаты. Меняется статус документа → переиндексация |

### Hit

| Поле | Тип | Смысл |
|---|---|---|
| chunk | Chunk | |
| score | float | финальный (после реранкера) |
| bm25_score, dense_score | float? | для отладки и eval |

### Дата актуальности

Статус «действует» зависит от даты. Берём её из конфига `REFERENCE_DATE` (по умолчанию — сегодня), **а не из системных часов**, иначе результаты eval зависят от дня запуска.

## 2. Retrieval API (🔎 разработчик 3 → 🤖 разработчик 4, 🧭 капитан; индекс строит 📄 разработчик 2)

| Функция | Параметры | Возвращает |
|---|---|---|
| `search` | query; k = 8; allowed_access (пусто = без ограничений); statuses (по умолчанию `active` + `unknown`); doc_ids (искать только в них) | `SearchResult` |
| `get_document` | doc_id | Document |
| `get_versions` | doc_id | все редакции, от новой к старой |
| `get_section` | doc_id, section | Chunk или пусто |
| `list_documents` | allowed_access | список Document |

### SearchResult

| Поле | Тип | Смысл |
|---|---|---|
| hits | list[Hit] | найденное с учётом прав и статусов |
| restricted | list[{doc_id, doc_title}] | документы, которые **нашлись бы**, но закрыты правами пользователя. Без текста! Нужно агенту, чтобы сказать «документ есть, но у вас нет доступа» |
| top_score | float? | лучший скор реранкера среди hits |

> Почему не `only_active: bool`: нужно решить, что делать со `status=unknown` (дат нет). Решение: ищем по `active` и `unknown`, агент предупреждает про `unknown`.

## 3. Agent API (🤖 разработчик 4 → 🧭 капитан)

### UserContext

| Поле | Тип |
|---|---|
| user_id, name, department | str |
| allowed_access | list[str] |

### Citation

| Поле | Тип | Смысл |
|---|---|---|
| chunk_id | str | для подсветки пункта в просмотрщике |
| doc_id, doc_title | str | |
| section | str | |
| version | str? | для подписи «п. 3.2.1 · ред. 3 ✓» |
| effective_from | date? | |
| status | DocStatus | |
| quote | str | короткая дословная цитата из пункта |

### AgentStep

| Поле | Тип | Смысл |
|---|---|---|
| name | str | analyze / plan / retrieve / grade / rewrite / check_actuality / check_conflicts / generate / verify / finalize / refuse |
| title | str | человекочитаемо: «Ищу: порядок подписания договоров» |
| detail | dict | что угодно для отладки: подзапросы, top-скоры, отброшенные редакции |
| started_at | datetime | |
| duration_ms | int | |

`AnswerStatus` — одно из: `answered`, `partial`, `refused`, `conflict`.

### AgentResult

| Поле | Тип | Смысл |
|---|---|---|
| status | AnswerStatus | |
| answer | str | markdown; цитаты в тексте **только как `[1]`, `[2]`** |
| citations | list[Citation] | позиция в списке (с 1) = номер `[n]` в тексте |
| reason | str? | почему отказ / в чём противоречие |
| steps | list[AgentStep] | |
| metrics | {n_hits, top_score, n_retrieval_rounds} | сразу для лога — backend не выковыривает их из `steps` |
| model | str | `qwen3:14b` |
| latency_ms | int | |

### Функции

| Функция | Параметры | Возвращает |
|---|---|---|
| `run_agent` | question, user: UserContext, history (последние реплики, может быть пусто) | AgentResult |
| `run_agent_stream` | то же | поток: несколько AgentStep, последним — AgentResult |

## 4. HTTP API (🧭 backend → 🧭 frontend)

| Метод | Путь | Тело / параметры | Ответ |
|---|---|---|---|
| GET | `/health` | — | `{db, qdrant, llm: "ok"/"down"}` |
| GET | `/auth/users` | — | список мок-пользователей для экрана входа |
| POST | `/auth/login` | `{user_id}` | `{token, user}` |
| GET | `/auth/me` | header `Authorization: Bearer` | `user` |
| POST | `/chat/sessions` | — | `{session_id}` |
| GET | `/chat/sessions` | — | список сессий пользователя |
| GET | `/chat/sessions/{id}` | — | сообщения сессии |
| POST | `/chat/ask` | `{session_id, question}` | `AgentResult` + `message_id` |
| POST | `/chat/ask/stream` | `{session_id, question}` | **SSE**, см. ниже |
| POST | `/feedback` | `{message_id, value: 1/-1, comment?}` | `{ok}` |
| GET | `/documents` | `?status=active` | список `Document` (с учётом прав) |
| GET | `/documents/{doc_id}` | — | `Document` + все чанки (для просмотрщика) |
| GET | `/documents/{doc_id}/versions` | — | история редакций |
| GET | `/analytics/summary` | `?from&to` | KPI: всего, % отвечено, % отказов, 👍/👎, среднее время |
| GET | `/analytics/failed` | `?limit` | неудачные запросы с причиной |
| GET | `/analytics/topics` | — | топ тем/кластеров |
| GET | `/analytics/gaps` | — | «чего нет в базе» |
| GET | `/analytics/timeline` | — | запросы и время ответа по дням/часам |
| POST | `/admin/reindex` | — | запуск переиндексации (**только роль admin**) |

`/analytics/*` и `/admin/*` — только для пользователя с ролью admin (на демо это видно как «права»).

### SSE-события `/chat/ask/stream`

| event | data |
|---|---|
| `step` | AgentStep, например `name=retrieve`, `title="Ищу: лимиты полномочий"`, `detail.top_score=0.82`, `duration_ms=340` |
| `step` | `name=check_actuality`, `title="Отбросил ред. 2 (заменена ред. 3 от 01.03.2026)"` |
| `result` | AgentResult + `message_id` |
| `error` | `{message: "LLM недоступна"}` |

## 5. Лог-событие (🧭 пишет → 🔎 читает)

Таблица `query_logs` (SQLite):

| Поле | Тип | Описание |
|---|---|---|
| id | str | = message_id |
| ts | datetime | время запроса |
| user_id, department | str | кто спросил |
| session_id | str | |
| question | str | |
| status | str | answered / partial / refused / conflict / error |
| answer | str | |
| citations | json | список Citation |
| n_hits | int | из `AgentResult.metrics` |
| top_score | float | из `AgentResult.metrics` |
| n_retrieval_rounds | int | из `AgentResult.metrics` |
| restricted_docs | json | документы, закрытые правами (для аналитики «спрашивают то, к чему нет доступа») |
| latency_ms | int | |
| model | str | |
| feedback | int / null | +1 / −1 |
| feedback_comment | str / null | |

Таблица `agent_steps`: `message_id, idx, name, title, detail(json), duration_ms`.

**Неудачный поиск** (для аналитики) = любое из: `status in (refused, error)`, `top_score < LOW_SCORE_THRESHOLD`, `feedback = -1`, `n_retrieval_rounds ≥ 2`.

## 6. Тестовый набор (готовит Claude, проверяет и использует 🔎 → все)

`eval/datasets/<name>.jsonl`, одна строка = один вопрос. Поля:

| Поле | Пример |
|---|---|
| id | `q001` |
| question | «Кто подписывает агентский договор?» |
| expected_answer | «Директор по продажам при сумме до 1 млн, свыше — генеральный директор» |
| expected_sources | `[{doc_id: "reg-0042", section: "3.2.1"}]` |
| category | см. ниже |
| expected_status | `answered` |
| user_id | от чьего имени спрашиваем (важно для `access`) |
| notes | |

`category`: `single` (один пункт) · `multi` (несколько регламентов) · `outdated` (ловушка на старую редакцию) · `conflict` (противоречие) · `no_answer` (ответа в базе нет) · `access` (зависит от прав) · `license` (правила лицензирования продуктов).

Тестовые вопросы — это **данные**, а не код, их можно готовить до старта. Но реальные данные дадут только 26.11.
