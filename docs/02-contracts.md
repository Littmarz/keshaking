# Контракты между модулями

> Это **главный документ для параллельной работы**. Каждый пишет свой модуль под эти интерфейсы, а пока соседний модуль не готов, использует заглушку (mock).
> Изменения — только через PR с ревью **всех четверых**. В коде лежит в `core/schemas.py` (создаём 26.11).

## 1. Данные (📄 разработчик 2 создаёт)

```python
from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel

DocStatus = Literal["active", "superseded", "cancelled", "unknown"]

class Document(BaseModel):
    doc_id: str                  # "reg-0042"
    title: str                   # "Регламент согласования договоров"
    doc_type: str                # "регламент" | "приказ" | "положение" | "продукт" | ...
    number: str | None           # "№ 15-П"
    version: str | None          # "ред. 3"
    approved_at: date | None
    effective_from: date | None
    effective_to: date | None
    status: DocStatus            # вычисляется ingest-ом по логике актуальности
    status_reason: str | None    # "заменён приказом № 22 от 01.03.2026"
    supersedes: list[str] = []   # doc_id тех, кого заменяет
    superseded_by: str | None = None
    access_level: str = "all"    # "all" | "legal" | "sales" | "hr" | "management"
    source_path: str             # "data/raw/reg-0042.docx"

class Chunk(BaseModel):
    chunk_id: str                # "reg-0042::3.2.1"
    doc_id: str
    doc_title: str
    section: str                 # "3.2.1" — номер пункта, ОБЯЗАТЕЛЬНО для цитат
    section_title: str | None    # "Порядок подписания"
    heading_path: list[str]      # ["3. Подписание", "3.2 Агентские договоры"]
    text: str
    page: int | None
    # денормализовано из Document — нужно для фильтров в Qdrant:
    status: DocStatus
    effective_from: date | None
    access_level: str

class Hit(BaseModel):
    chunk: Chunk
    score: float                 # финальный (после реранкера)
    bm25_score: float | None = None
    dense_score: float | None = None
```

## 2. Retrieval API (🔎 разработчик 3 → 🤖 разработчик 4, 🧭 капитан; индекс строит 📄 разработчик 2)

```python
def search(
    query: str,
    k: int = 8,
    allowed_access: list[str] | None = None,   # None = без ограничений
    only_active: bool = True,
    doc_ids: list[str] | None = None,          # искать только в этих документах
) -> list[Hit]: ...

def get_document(doc_id: str) -> Document: ...
def get_versions(doc_id: str) -> list[Document]: ...          # все редакции, от новой к старой
def get_section(doc_id: str, section: str) -> Chunk | None: ...
def list_documents(allowed_access: list[str] | None = None) -> list[Document]: ...
```

## 3. Agent API (🤖 разработчик 4 → 🧭 капитан)

```python
class UserContext(BaseModel):
    user_id: str
    name: str
    department: str
    allowed_access: list[str]

class Citation(BaseModel):
    doc_id: str
    doc_title: str
    section: str
    quote: str                   # короткая дословная цитата из пункта
    status: DocStatus

class AgentStep(BaseModel):
    name: str                    # "analyze" | "plan" | "retrieve" | "rewrite" | "check_actuality" | ...
    title: str                   # человекочитаемо: "Ищу: порядок подписания договоров"
    detail: dict = {}            # что угодно для отладки: подзапросы, top-скоры, отброшенные редакции
    started_at: datetime
    duration_ms: int

AnswerStatus = Literal["answered", "partial", "refused", "conflict"]

class AgentResult(BaseModel):
    status: AnswerStatus
    answer: str                  # markdown, цитаты в тексте как [1], [2]
    citations: list[Citation]    # индекс в списке = номер [n] в тексте
    reason: str | None           # почему отказ / в чём противоречие
    steps: list[AgentStep]
    model: str                   # "qwen3:14b"
    latency_ms: int

def run_agent(question: str, user: UserContext, history: list[dict] | None = None) -> AgentResult: ...

# потоковая версия — отдаёт шаги по мере выполнения, последним — итог
def run_agent_stream(question: str, user: UserContext, history: list[dict] | None = None
                     ) -> Iterator[AgentStep | AgentResult]: ...
```

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
| POST | `/admin/reindex` | — | запуск переиндексации |

### SSE-события `/chat/ask/stream`

```
event: step
data: {"name":"retrieve","title":"Ищу: лимиты полномочий","detail":{"top_score":0.82},"duration_ms":340}

event: step
data: {"name":"check_actuality","title":"Отбросил ред. 2 (заменена ред. 3 от 01.03.2026)", ...}

event: result
data: { ...AgentResult..., "message_id": "m_123" }

event: error
data: {"message": "LLM недоступна"}
```

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
| n_hits | int | сколько чанков нашли |
| top_score | float | лучший скор реранкера |
| n_retrieval_rounds | int | сколько раз переформулировали |
| latency_ms | int | |
| model | str | |
| feedback | int / null | +1 / −1 |
| feedback_comment | str / null | |

Таблица `agent_steps`: `message_id, idx, name, title, detail(json), duration_ms`.

**Неудачный поиск** (для аналитики) = любое из: `status in (refused, error)`, `top_score < порога`, `feedback = -1`, `n_retrieval_rounds ≥ 2`.

## 6. Тестовый набор (готовит Claude, проверяет и использует 🔎 → все)

`eval/datasets/<name>.jsonl`, одна строка = один вопрос:

```json
{"id":"q001","question":"Кто подписывает агентский договор?","expected_answer":"Директор по продажам при сумме до 1 млн, свыше — генеральный директор","expected_sources":[{"doc_id":"reg-0042","section":"3.2.1"}],"category":"single","expected_status":"answered","notes":""}
```

`category`: `single` (один пункт) · `multi` (несколько регламентов) · `outdated` (ловушка на старую редакцию) · `conflict` (противоречие) · `no_answer` (ответа в базе нет) · `access` (зависит от прав) · `license` (правила лицензирования продуктов).
