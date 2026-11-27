# Контракты между модулями

> **Главный документ для параллельной работы.** Каждый пишет свой модуль под эти интерфейсы, а пока соседний модуль не готов — работает на заглушке.
> Код — `core/schemas.py`, пишется по этим таблицам. Правки контракта и `core/schemas.py` — **только по согласованию команды** (PR + ревью двух человек: автор модуля-потребителя + капитан).
> Обновлено 26.11: уточнение, обращения в службу качества, подтверждённые записи, дирекция и этап. Перед изменением общих схем — посмотреть на **реальные данные** из DOCX.

Обозначения: `str?` — может быть пусто; `list[X]` — список; `date` — дата; `datetime` — дата и время.

## 1. Данные (📄 разработчик 2)

`DocStatus` — `active` | `superseded` | `cancelled` | `unknown`. Для нашего корпуса по умолчанию **`unknown`**: редакций и дат вступления в силу нет, дата изменения файла на Диске — не дата вступления.

### Document

| Поле | Тип | Смысл |
|---|---|---|
| doc_id | str | стабильный: `ib-project`, `dbrct-project`, `tech-project`, `ib-presale` |
| title | str | «Проектная деятельность Дирекции ИБ» |
| doc_type | str | регламент / положение / … |
| directorate | str? | `ИБ` / `ДБРЦТ` / `Техдирекция` — **из документа и его структуры** |
| stage | str? | этап по умолчанию для документа: `проект` / `пресейл` / … |
| number, version | str? | если есть в тексте |
| effective_from, effective_to | date? | только если явно указано в тексте |
| status | DocStatus | см. выше |
| status_reason | str? | |
| supersedes | list[str] | |
| superseded_by | str? | |
| access_level | str | `all` по умолчанию (матрицы прав нет; права — после обязательных сценариев) |
| source_path | str | `data/raw/<файл>.docx` (локально, не в git) |

### Chunk

| Поле | Тип | Смысл |
|---|---|---|
| chunk_id | str | `ib-presale::1.3` — **стабильный** между переиндексациями |
| source_type | str | `regulation` — пункт регламента; `qa_entry` — подтверждённая запись службы качества |
| doc_id, doc_title | str | для `qa_entry` — регламент, к которому привязана запись |
| section | str | номер пункта `1.3` — **обязателен** для цитаты |
| section_title | str? | |
| heading_path | list[str] | `["1. Пресейл", "1.3 Состав ТКП"]` |
| text | str | чистый текст пункта **со всеми вложенными подпунктами, условиями и списками** (ветки «до 30% включительно» / «свыше 30%» в одном чанке) |
| directorate, stage | str? | область применения пункта (денормализовано, для фильтров и check_scope) |
| status, access_level | — | денормализовано из Document |
| qa_entry_id | str? | для `source_type = qa_entry` |

### Hit

| Поле | Тип | Смысл |
|---|---|---|
| chunk | Chunk | |
| score | float | после реранкера |
| bm25_score, dense_score | float? | отладка |
| parents | list[{section, section_title, text}] | родительские пункты вложенного (1.1.1 → 1.1): условие часто задаёт родитель («На данном этапе SS проводит: …») |

## 2. Retrieval API (🔎 разработчик 3; индекс строит 📄)

| Функция | Параметры | Возвращает |
|---|---|---|
| `search` | query; k = 8; allowed_access (пусто = без ограничений); statuses (по умолчанию `active` + `unknown` — **иначе весь наш корпус отфильтруется**); doc_ids; directorate?; stage?; include_qa = true | `SearchResult` |
| `get_document` | doc_id | Document |
| `get_versions` | doc_id | редакции, от новой к старой (у нас обычно одна) |
| `get_section` | doc_id, section | Chunk или пусто |
| `list_documents` | allowed_access | список Document |
| `upsert_qa_entry` | QAEntry (только `confirmed`) | — публикует запись в индекс |

### SearchResult

| Поле | Тип | Смысл |
|---|---|---|
| hits | list[Hit] | регламенты и подтверждённые записи вместе, у каждого `source_type` |
| restricted | list[{doc_id, doc_title}] | нашлось бы, но закрыто правами (пока пусто) |
| top_score | float? | |
| coverage | float? | доля «веса» слов запроса (idf), найденных в лучшем пункте |
| low_confidence | bool | подсказка «в регламентах, похоже, ответа нет» (явно посторонний вопрос). **Не решение** — эскалацию решает агент после grade моделью |
| scope | list[str] | дирекции, распознанные в вопросе («ИБ», «ДБРЦТ», «Техдирекция») — пригодится для clarify/check_scope |
| mode | str | `bm25` / `hybrid` / `hybrid+rerank` |
| ambiguous_scope | bool | подсказка для **уточнения** (сценарий 2): подходящие пункты из разных дирекций/этапов, а в вопросе область не названа. Агент сравнивает тексты: правила разные → `needs_clarification`; одинаковые (как «кто отвечает за коммуникации с заказчиком») → отвечает, цитируя оба |
| scope_options | list[{directorate, stage, label, doc_id, doc_title, section, score}] | варианты для уточняющего вопроса и кнопок в чате |

> Высокий балл поиска ≠ применимость. Проверку дирекции и этапа делает агент (`check_scope`).

## 3. Agent API (🤖 разработчик 4 → 🧭 капитан)

### UserContext

| Поле | Тип |
|---|---|
| user_id, name, department | str |
| role | `employee` / `quality` / `admin` |
| allowed_access | list[str] |

### Citation

| Поле | Тип | Смысл |
|---|---|---|
| chunk_id | str | подсветка пункта в просмотрщике |
| source_type | str | `regulation` / `qa_entry` |
| doc_id, doc_title, section | str | |
| directorate, stage | str? | показать область применения рядом с цитатой |
| quote | str | короткая дословная цитата (проверяется кодом) |
| qa_entry_id | str? | для подтверждённого знания: «подтверждено службой качества, обращение №…» |

### AgentStep

| Поле | Тип | Смысл |
|---|---|---|
| name | str | analyze / clarify / plan / retrieve / grade / rewrite / check_scope / check_conflicts / generate / verify / escalate / finalize |
| title | str | человекочитаемо: «Уточняю дирекцию и этап» |
| detail | dict | подзапросы, top-скоры, отброшенные пункты |
| started_at | datetime | |
| duration_ms | int | |

`AnswerStatus`:

| Статус | Когда |
|---|---|
| `answered` | ответ по регламентам и/или подтверждённым записям |
| `partial` | ответ на часть вопроса, по остальному — причина |
| `needs_clarification` | нужен факт от сотрудника; в `clarification` — один конкретный вопрос; ход закончен |
| `escalated` | ответа нет → создано обращение в службу качества; в `ticket` — номер и что дальше |
| `conflict` | два применимых источника противоречат, правило не решает; показываем оба и причину |
| `refused` | ответить нельзя: **вне базы знаний** (маржа, премия, «сделай фоллоуап» — «обратитесь к руководителю», без эскалации, D-011) или неоднозначность не разрешилась за 2 раунда (тогда — варианты списком и/или эскалация, D-012) |

### AgentResult

| Поле | Тип | Смысл |
|---|---|---|
| status | AnswerStatus | |
| answer | str | markdown; цитаты в тексте **только `[1]`, `[2]`** |
| citations | list[Citation] | позиция (с 1) = номер `[n]` |
| reason | str? | почему отказ / в чём противоречие / почему эскалация |
| clarification | {question, options?} ? | для `needs_clarification`: вопрос и, если уместно, варианты-кнопки |
| ticket | {ticket_id, created: bool, message} ? | для `escalated`: номер обращения; `created=false`, если обращение по этому вопросу уже было |
| steps | list[AgentStep] | |
| metrics | {n_hits, top_score, n_retrieval_rounds, **prompt_tokens, completion_tokens, llm_calls**} | для логов и **экономики** (сумма usage по всем вызовам LLM за запрос) |
| model | str | `qwen3:8b` |
| latency_ms | int | |

### Функции

| Функция | Параметры | Возвращает |
|---|---|---|
| `run_agent` | question, user, history | AgentResult |
| `run_agent_stream` | то же | несколько AgentStep, последним — AgentResult |

`history` — последние реплики сессии `[{role, content, status?}]`. Если последний ответ агента был `needs_clarification`, analyze склеивает исходный вопрос и ответ пользователя в один самостоятельный вопрос.

**Обращение создаёт backend, не агент**: агент возвращает `status = escalated` и `reason`; `agent_service` вызывает `create_ticket(session_id, question)` — идемпотентно по (session_id, нормализованный вопрос) — и подставляет `ticket` в результат.

## 4. Служба качества (🧭 хранение и форма, 🔎 публикация в поиск)

### Ticket (обращение)

| Поле | Тип | Смысл |
|---|---|---|
| ticket_id | str | `Q-0007` — показываем сотруднику |
| created_at | datetime | |
| user_id, session_id | str | |
| question | str | вопрос после склейки с уточнениями |
| context | json | что искал агент: подзапросы, ближайшие пункты |
| status | `open` / `answered` / `published` / `rejected` | |
| dedup_key | str | нормализованный вопрос + session_id — **одно обращение на вопрос** |
| email_to, email_cc | str | адрес службы качества (на демо — Mailpit) и **пользователь в копии** (кик-офф: эскалация письмом, D-010) |
| email_message_id | str? | Message-ID письма — по нему связываем ответ эксперта из почты с обращением |

### QAEntry (подтверждённое знание)

| Поле | Тип | Смысл |
|---|---|---|
| qa_entry_id | str | |
| ticket_id | str | |
| question | str | |
| answer | str | ответ сотрудника качества |
| doc_id, section | str? | **привязка к регламенту** |
| directorate, stage | str? | область применения |
| confirmed_by, confirmed_at | str, datetime | |
| status | `draft` / `confirmed` | в поиск попадает **только `confirmed`** |
| is_demo | bool | учебный ответ для демо — так и подписываем |

## 5. HTTP API (🧭 backend → 🧭 frontend)

| Метод | Путь | Тело / параметры | Ответ |
|---|---|---|---|
| GET | `/health` | — | `{db, search, llm: "ok"/"down"}` |
| GET | `/auth/users` | — | мок-пользователи (сотрудники и сотрудник качества) |
| POST | `/auth/login` | `{user_id, password}` | `{token, user}` |
| GET | `/auth/me` | Bearer | user |
| POST | `/chat/sessions` | — | `{session_id}` |
| GET | `/chat/sessions` · `/chat/sessions/{id}` | — | сессии / сообщения |
| POST | `/chat/ask` | `{session_id, question}` | AgentResult + `message_id` |
| POST | `/chat/ask/stream` | `{session_id, question}` | **SSE** |
| POST | `/feedback` | `{message_id, value: 1/-1, comment?}` | `{ok}` |
| GET | `/documents` · `/documents/{doc_id}` | — | список / документ + пункты |
| GET | `/tickets` | `?status=open` | очередь (роль `quality`) |
| GET | `/tickets/{id}` | — | обращение + контекст поиска |
| POST | `/tickets/{id}/answer` | `{answer, doc_id?, section?, directorate?, stage?}` | QAEntry (`draft`) |
| POST | `/qa/{qa_entry_id}/confirm` | — | QAEntry (`confirmed`) → `upsert_qa_entry` |
| GET | `/qa` | — | опубликованные записи |
| GET | `/analytics/summary` | — | KPI (после обязательных сценариев) |

Ошибки связи с моделью или поиском — событие `error` / HTTP 503 с текстом «Модель недоступна», **не** ответ агента.

### SSE `/chat/ask/stream`

| event | data |
|---|---|
| `step` | AgentStep |
| `result` | AgentResult + `message_id` |
| `error` | `{message}` |

## 6. Логи (🧭 пишет → 🔎 читает)

`query_logs`: `id (= message_id), ts, user_id, session_id, question, status, answer, citations (json), clarification (json), ticket_id, n_hits, top_score, n_retrieval_rounds, latency_ms, model, feedback, feedback_comment`.
`agent_steps`: `message_id, idx, name, title, detail (json), duration_ms`.

Логи нужны для отладки, демо и **расчёта стоимости** (задержка, токены, запросов в день).

## 7. Контрольный набор (🔎 ведёт, все используют)

`eval/datasets/control.jsonl`, строка = один сценарий:

| Поле | Смысл |
|---|---|
| id | `c01` |
| turns | список реплик пользователя по порядку (для уточнения — две и больше) |
| expected_status | итоговый статус; для многоходовых — по ходам: `["needs_clarification", "answered"]` |
| expected_sources | `[{doc_id, section}]` |
| must_contain | ключевые факты, которые должны быть в ответе (`"более пяти рабочих дней"`) |
| must_not_cite | источники, которых быть не должно (чужая дирекция) |
| category | `single` · `clarify` · `multi` · `boundary` · `escalate` · `conflict` |
| notes | |

Эталонного набора от организаторов **нет** — набор составляем сами по 4 DOCX.
