# 🧭 Капитан — Backend + Frontend + сборка

> **Твоя зона:** `backend/` · `frontend/` · `deploy/` + координация команды
> **Твой главный результат:** работающий стенд — открыл браузер, залогинился, задал вопрос, увидел шаги агента, ответ с цитатами, кликнул в цитату → документ с подсвеченным пунктом, переключил пользователя → другие права, открыл дашборд аналитики. Всё поднимается одной командой без интернета.
> **Критерии жюри, за которые отвечаешь:** UX и демо, права доступа (бонус), корректность реализации, визуализация агентности и аналитики.
> **Нагрузка:** ~48 ч разработки (как у остальных, см. [README.md](README.md#распределение-часов)).

**Твои парные задачи (👥):**
- **П3. Аналитика** — с 🔎 разработчиком 3: он считает (неудачные поиски, пробелы, темы, KPI), ты делаешь `/analytics/*` и дашборд.
- **П4. Офлайн-сборка** — ты ведёшь, 📄 разработчик 2 даёт сервис Qdrant и предзагрузку моделей поиска, 🤖 разработчик 4 — сервис LLM.

**Без кода (делает Claude по твоей просьбе):** тексты вопросов организаторам, описание продукта, сценарий видео, синтетические документы, тестовые вопросы. **Запись видео** по сценарию — ты.

---

## Содержание

1. [Капитанские задачи (координация)](#1-капитанские-задачи-координация)
2. [Что установить и скачать](#2-что-установить-и-скачать)
3. [Что изучить](#3-что-изучить)
4. [План подготовки по дням](#4-план-подготовки-по-дням-2111--2511)
5. [Backend: структура и назначение каждого файла](#5-backend-структура-и-назначение-каждого-файла)
6. [Frontend: структура, экраны, компоненты](#6-frontend-структура-экраны-компоненты)
7. [Deploy: docker-compose и офлайн](#7-deploy-docker-compose-и-офлайн)
8. [Монорепо: как собрать](#8-монорепо-как-собрать)
9. [Бэклог на хакатон (после ТЗ)](#9-бэклог-на-хакатон-после-тз)
10. [Самопроверка к 26.11](#10-самопроверка-к-2611)
11. [Риски и подводные камни](#11-риски-и-подводные-камни)

---

## 1. Капитанские задачи (координация)

### Сб 21.11 — сразу
- [ ] Отправить всем этот архив, попросить каждого открыть свой файл в `docs/team/`
- [ ] Создать общий чат (если ещё нет), закрепить ссылку на репо
- [ ] **Опрос по железу**: у кого какая GPU / VRAM / RAM / ОС → вписать в [README.md](README.md). Решить:
  - есть хоть у кого-то NVIDIA ≥ 12–16 ГБ → это **машина для LLM** (на ней же демо)
  - нет → арендовать GPU-сервер на время хакатона (решить до 25.11, бюджет — с командой)
- [ ] Создать **GitHub-репозиторий** (пока private), добавить всех, защитить `main` (Settings → Branches → require PR + 1 review)
- [ ] Залить туда этот набор файлов как первый коммит
- [ ] Создать **GitHub Project** (доска) с колонками To do / In progress / Review / Done
- [ ] Назначить время ежедневного синка (15 минут)

### Вс 22 – Ср 25
- [ ] Каждый вечер — короткая проверка в чате: кто что прошёл по своему чек-листу
- [ ] Ср 25.11 вечером — **созвон 1 час**: каждый показывает свою песочницу (5 мин), финализируем [../02-contracts.md](../02-contracts.md)
- [ ] Отправить вопросы организаторам (текст готовит Claude) ([../05-questions-to-organizers.md](../05-questions-to-organizers.md))

### 26.11 — день ТЗ
- [ ] Прочитать ТЗ первым, скинуть в чат, созвон 30 мин
- [ ] Обновить контракты, если ТЗ что-то меняет
- [ ] Создать `core/schemas.py` из контрактов, нарезать задачи на доску

---

## 2. Что установить и скачать

### Установить (сверх общего списка из [00-everyone.md](00-everyone.md))

| Что | Зачем | Проверка |
|---|---|---|
| **Node.js 22 LTS** (через **nvm** / **fnm**) | фронтенд | `node -v` |
| **pnpm** (`npm i -g pnpm`) | пакеты фронта, быстрее npm | `pnpm -v` |
| **DB Browser for SQLite** или расширение VS Code «SQLite Viewer» | смотреть логи в БД | — |
| **Bruno** / **Postman** / **Thunder Client** | дёргать API руками | — |
| **make** (Linux/Mac есть, на Windows — в WSL) | короткие команды `make up` | `make -v` |
| **OBS Studio** | запись демо-видео (сценарий готовит Claude) | — |

### Скачать заранее (чтобы на хакатоне не ждать)

```bash
# docker-образы, которые пойдут в compose
docker pull python:3.12-slim
docker pull node:22-alpine
docker pull nginx:alpine
docker pull qdrant/qdrant:latest
docker pull ollama/ollama:latest
```

---

## 3. Что изучить

> Время — ориентир. Учи **на практике в песочнице**, а не просто читая.

### Backend (~6 ч)

| Тема | Зачем нам | Где | Время |
|---|---|---|---|
| **FastAPI**: роутеры (`APIRouter`), `Depends`, pydantic-модели запросов/ответов, `lifespan` | весь API | fastapi.tiangolo.com → Tutorial (первые ~15 разделов) | 2 ч |
| **Server-Sent Events (SSE)** в FastAPI: `StreamingResponse` или библиотека `sse-starlette` | стримим шаги агента на фронт — главная «вау»-фишка агентности | доки sse-starlette на GitHub + любой пример «FastAPI SSE» | 1 ч |
| **Как запустить синхронный код в async**: `run_in_threadpool`, `asyncio.to_thread` | агент/LLM может быть синхронным, нельзя блокировать сервер | раздел «Concurrency and async / await» в доках FastAPI | 30 мин |
| **SQLModel** (SQLAlchemy + pydantic) + SQLite | таблицы логов, сообщений, фидбека | sqlmodel.tiangolo.com → Tutorial | 1 ч |
| **pydantic-settings** | конфиг из `.env` | docs.pydantic.dev → Settings Management | 20 мин |
| **CORS** в FastAPI | фронт на другом порту | раздел CORS в доках FastAPI | 10 мин |
| **pytest** + `TestClient` | пара тестов на ключевые ручки | раздел Testing в доках FastAPI | 30 мин |

### Frontend (~6 ч)

| Тема | Зачем нам | Где | Время |
|---|---|---|---|
| **React + TypeScript** (если не знаешь): компоненты, props, `useState`, `useEffect` | всё | react.dev → Learn | 2 ч |
| **Vite** — создать проект `pnpm create vite@latest frontend -- --template react-ts` | сборка | vite.dev → Getting Started | 15 мин |
| **Tailwind CSS** | быстро и красиво | tailwindcss.com → Installation → Vite | 30 мин |
| **shadcn/ui** — готовые компоненты (Button, Card, Sheet, Tabs, Badge, Table) | не рисовать с нуля | ui.shadcn.com → Installation → Vite | 30 мин |
| **TanStack Query** (`useQuery`, `useMutation`) | запросы к API, кэш, загрузка | tanstack.com/query → Quick Start | 40 мин |
| **Чтение SSE из POST-запроса**: `fetch` + `ReadableStream` или библиотека `@microsoft/fetch-event-source` (нативный `EventSource` умеет только GET) | стрим шагов агента | README библиотеки на GitHub | 40 мин |
| **React Router** | страницы: вход, чат, документы, аналитика | reactrouter.com | 20 мин |
| **Recharts** | графики на дашборде | recharts.org → Examples | 30 мин |
| **react-markdown** | ответ агента в markdown | README на GitHub | 10 мин |

> **Если React совсем не знаешь и не успеваешь** — запасной план: Streamlit (`ui_streamlit/`), чат + источники + дашборд делаются за день. Реши к Пн 23.11 вечером. Но React-интерфейс сильно выигрывает на видео.

### Docker (~1.5 ч)

| Тема | Где | Время |
|---|---|---|
| Dockerfile для Python (uv) и для фронта (multi-stage: сборка → nginx) | docs.docker.com, гайд uv «Using uv in Docker» | 45 мин |
| docker compose: `services`, `volumes`, `depends_on`, `healthcheck`, `networks`, GPU (`deploy.resources.reservations.devices`) | docs.docker.com/compose | 45 мин |

---

## 4. План подготовки по дням (21.11 – 25.11)

### Сб 21.11 (~3 ч)
- [ ] Капитанские задачи из раздела 1
- [ ] Общий чек-лист [00-everyone.md](00-everyone.md)
- [ ] Установить Node, pnpm, скачать docker-образы

### Вс 22.11 (~4 ч) — Backend
- [ ] Пройти FastAPI Tutorial (роутеры, Depends, модели)
- [ ] В `sandbox/captain/api/` сделать игрушку:
  - `GET /health`
  - `POST /ask` → возвращает фейковый ответ
  - `POST /ask/stream` → SSE: 4 события `step` с `asyncio.sleep(0.7)` и одно `result`
- [ ] Проверить стрим через `curl -N -X POST localhost:8000/ask/stream -H 'Content-Type: application/json' -d '{"question":"тест"}'`
- [ ] SQLModel: таблица `QueryLog`, записать и прочитать

### Пн 23.11 (~4 ч) — Frontend
- [ ] Пройти React Learn (если нужно), создать Vite-проект в `sandbox/captain/web/`
- [ ] Подключить Tailwind + shadcn/ui
- [ ] Нарисовать **макеты 4 экранов** (Figma, бумага или сразу в коде) — см. раздел 6
- [ ] **Решение: React или Streamlit** → записать в [../decisions.md](../decisions.md)

### Вт 24.11 (~4 ч) — Связка
- [ ] Фронт-игрушка читает SSE из бэк-игрушки: шаги появляются по одному, потом карточка ответа
- [ ] Чип цитаты → боковая панель (shadcn `Sheet`) с текстом пункта
- [ ] Мини-график Recharts на фейковых данных

### Ср 25.11 (~3 ч) — Сборка и контракты
- [ ] Черновик `docker-compose.yml` (раздел 7): qdrant + ollama + backend + frontend, проверить, что поднимается
- [ ] Перечитать [../02-contracts.md](../02-contracts.md) глазами бэкенда: всё ли нужно фронту есть в `AgentResult`?
- [ ] Вечером — созвон команды
- [ ] Самопроверка (раздел 10)

---

## 5. Backend: структура и назначение каждого файла

> Стек: **FastAPI · SQLModel · SQLite · pydantic-settings · sse-starlette · uvicorn**
> Ключевой принцип: backend **не знает**, как устроен агент. Он вызывает `agent.run_agent_stream()` по контракту. Пока агента нет — `AGENT_MODE=mock`.

```
backend/
├── README.md                     # как запустить, переменные окружения, примеры curl
├── pyproject.toml                # зависимости backend (часть uv workspace)
├── Dockerfile
├── .env.example
│
├── app/
│   ├── __init__.py
│   ├── main.py                   # create_app(): FastAPI, CORS, подключение роутеров, lifespan
│   ├── config.py                 # Settings (pydantic-settings): все URL, пути, режимы
│   ├── deps.py                   # Depends: get_session (БД), get_current_user, get_agent_service
│   ├── security.py               # мок-токены: выдать / проверить (подписанный user_id, без паролей)
│   │
│   ├── api/                      # ── HTTP-ручки, тонкий слой: валидация → сервис → ответ ──
│   │   ├── __init__.py           # api_router = все роутеры вместе
│   │   ├── health.py             # GET /health — пинг БД, Qdrant, LLM
│   │   ├── auth.py               # GET /auth/users, POST /auth/login, GET /auth/me
│   │   ├── chat.py               # сессии + POST /chat/ask + POST /chat/ask/stream (SSE)
│   │   ├── feedback.py           # POST /feedback
│   │   ├── documents.py          # GET /documents, /documents/{id}, /documents/{id}/versions
│   │   ├── analytics.py          # GET /analytics/summary|failed|topics|gaps|timeline
│   │   └── admin.py              # POST /admin/reindex, GET /admin/status
│   │
│   ├── schemas/                  # ── pydantic-модели запросов/ответов HTTP (DTO) ──
│   │   ├── __init__.py
│   │   ├── auth.py               # LoginRequest, LoginResponse, UserOut
│   │   ├── chat.py               # AskRequest, AskResponse, SessionOut, MessageOut
│   │   ├── feedback.py           # FeedbackRequest
│   │   ├── documents.py          # DocumentOut, DocumentDetailOut (с чанками)
│   │   └── analytics.py          # SummaryOut, FailedQueryOut, TopicOut, GapOut, TimelinePoint
│   │                             # (AgentResult, Citation, Document — импортируем из core.schemas)
│   │
│   ├── db/                       # ── хранилище ──
│   │   ├── __init__.py
│   │   ├── session.py            # engine, create_db_and_tables(), get_session()
│   │   ├── models.py             # таблицы SQLModel (см. ниже)
│   │   └── seed.py               # мок-пользователи и (для отладки) фейковые логи
│   │
│   ├── services/                 # ── бизнес-логика, без HTTP ──
│   │   ├── __init__.py
│   │   ├── agent_service.py      # обёртка над агентом: mock/real, стрим, таймаут, ошибки
│   │   ├── chat_service.py       # сессии, история сообщений (передаём агенту последние N)
│   │   ├── log_service.py        # запись QueryLog + AgentStepLog, обновление feedback
│   │   ├── access_service.py     # user → allowed_access (роль/отдел → уровни документов)
│   │   ├── document_service.py   # прокси к retrieval.get_document / list_documents с фильтром прав
│   │   └── analytics_service.py  # вызывает функции из analytics/ (🔎 разработчик 3) или SQL-агрегаты
│   │
│   ├── mocks/
│   │   └── fake_agent.py         # фейковый run_agent_stream: 5 шагов со sleep + AgentResult
│   │                             # 3 сценария по ключевому слову: обычный ответ / отказ / конфликт
│   └── utils/
│       ├── sse.py                # хелпер: AgentStep|AgentResult → событие SSE
│       └── timing.py             # контекстный менеджер для замеров latency
│
└── tests/
    ├── conftest.py               # TestClient, временная SQLite, AGENT_MODE=mock
    ├── test_health.py
    ├── test_auth.py              # логин, /me, неверный токен → 401
    ├── test_chat.py              # ask возвращает AgentResult, лог записался
    ├── test_stream.py            # стрим отдаёт step… и result
    └── test_access.py            # юзер без прав не видит документ legal
```

### `app/config.py` — что в настройках

| Переменная | Пример | Зачем |
|---|---|---|
| `AGENT_MODE` | `mock` / `real` | работать без агента |
| `DATABASE_URL` | `sqlite:///./data/app.db` | логи |
| `QDRANT_URL` | `http://qdrant:6333` | health + документы |
| `LLM_BASE_URL` | `http://ollama:11434/v1` | health |
| `LLM_MODEL` | `qwen3:14b` | показываем в ответе и на дашборде |
| `CORS_ORIGINS` | `http://localhost:5173` | фронт в dev-режиме |
| `SECRET_KEY` | случайная строка | подпись мок-токенов |
| `AGENT_TIMEOUT_S` | `120` | не висим вечно |
| `HISTORY_TURNS` | `4` | сколько прошлых реплик отдавать агенту |
| `LOW_SCORE_THRESHOLD` | `0.3` | порог «неудачного поиска» (согласовать с 🔎) |

### `app/db/models.py` — таблицы

| Таблица | Поля | Для чего |
|---|---|---|
| `User` | id, name, department, role, allowed_access (json), avatar | мок-пользователи |
| `ChatSession` | id, user_id, title, created_at | история чатов в сайдбаре |
| `Message` | id, session_id, role (user/assistant), content, status, citations (json), created_at | переписка |
| `QueryLog` | см. [../02-contracts.md §5](../02-contracts.md) | **аналитика** |
| `AgentStepLog` | message_id, idx, name, title, detail (json), duration_ms | разбор, как агент думал |
| `Feedback` | message_id, value, comment, created_at | 👍/👎 |

### Мок-пользователи (`seed.py`) — для демонстрации прав

| user_id | Имя | Отдел | allowed_access |
|---|---|---|---|
| `u_admin` | Админ Системы | ИТ | `all, legal, sales, hr, management` |
| `u_legal` | Юрист Иванова | Юридический | `all, legal` |
| `u_sales` | Менеджер Петров | Продажи | `all, sales` |
| `u_new` | Стажёр Сидоров | Продажи | `all` |

> Уровни доступа уточняем после ТЗ (зависят от того, какие документы дадут). Демо: стажёр спрашивает про внутренний юр. регламент → «у вас нет доступа к документу X, обратитесь…», юрист — получает ответ.

### `services/agent_service.py` — логика

```text
ask_stream(question, user, session_id):
  1. user → UserContext (access_service)
  2. history = последние HISTORY_TURNS сообщений сессии
  3. сохранить Message(role=user)
  4. for item in agent.run_agent_stream(...)   # в threadpool, т.к. может быть синхронным
         if AgentStep  → yield SSE "step", копим шаги
         if AgentResult → сохранить Message(assistant) + QueryLog + AgentStepLog → yield SSE "result" (+message_id)
  5. исключение / таймаут → QueryLog(status=error) → yield SSE "error"
```

### Порядок разработки backend на хакатоне

1. `config` → `db` → `seed` → `health` → `auth` (1-й день)
2. `chat` + `mocks/fake_agent` + SSE (1-й день) → **фронт уже может работать**
3. `log_service` + `feedback` (2-й день)
4. `documents` + `access_service` (когда 🔎 даст `retrieval`)
5. `analytics` (когда 🔎 даст функции агрегации, до этого — простые SQL)
6. переключить `AGENT_MODE=real`, когда 🤖 даст `run_agent_stream`

---

## 6. Frontend: структура, экраны, компоненты

> Стек: **React 18+ · Vite · TypeScript · Tailwind · shadcn/ui · TanStack Query · React Router · Recharts · react-markdown · @microsoft/fetch-event-source · zustand**

### Экраны

| # | Экран | Что на нём | Зачем для жюри |
|---|---|---|---|
| 1 | **Вход** `/login` | Карточки мок-пользователей (имя, отдел, бейджи доступов) → клик = вход | Имитация корп. SSO + права доступа |
| 2 | **Чат** `/` | Слева — сессии. Центр — переписка. Под каждым ответом — **таймлайн шагов агента** (раскрывается), чипы цитат, статус-бейдж, 👍/👎. Справа — выезжающая панель с документом | **Агентность**, цитаты, честный отказ |
| 3 | **Документы** `/documents` | Таблица документов: название, тип, редакция, дата, **статус** (действует / заменён / отменён), доступ. Клик → просмотр с оглавлением по пунктам, история редакций | Логика актуальности наглядно |
| 4 | **Аналитика** `/analytics` | KPI-плитки, график запросов по дням, % отвеченных/отказов, таблица неудачных поисков с причиной, «чего нет в базе», топ тем, среднее время | Критерий «соответствие задаче + аналитика» |

Шапка на всех экранах: логотип, название модели (`qwen3:14b · локально`), текущий пользователь + **быстрый переключатель пользователя** (удобно на демо).

### Как выглядит ответ в чате

```
┌──────────────────────────────────────────────────────────────┐
│ ✅ Ответ найден                               12.4 с · 3 шага │
│                                                              │
│ Агентский договор свыше 1 млн ₽ подписывает генеральный      │
│ директор [1]; до 1 млн ₽ — директор по продажам по           │
│ доверенности [2].                                            │
│                                                              │
│ Источники:                                                   │
│ [1] Регламент согласования договоров, п. 3.2.1 · ред. 3 ✓    │
│ [2] Приказ № 15-П о полномочиях, п. 2.4 · действует ✓        │
│                                                              │
│ ▸ Как агент искал ответ (6 шагов)                     👍 👎   │
└──────────────────────────────────────────────────────────────┘
```

Варианты статусов: ✅ `answered` (зелёный) · 🟡 `partial` (жёлтый) · ⚠️ `conflict` (оранжевый, показываем оба источника рядом) · ⛔ `refused` (серый, с причиной и «что можно сделать»).

### Структура папок

```
frontend/
├── README.md                    # pnpm i / pnpm dev / pnpm build, переменные
├── package.json
├── pnpm-lock.yaml
├── vite.config.ts               # proxy /api → http://localhost:8000 в dev
├── tsconfig.json
├── tailwind.config.ts
├── components.json              # конфиг shadcn/ui
├── index.html
├── Dockerfile                   # multi-stage: node build → nginx
├── nginx.conf                   # отдать статику + проксировать /api на backend (+ отключить буфер для SSE!)
├── .env.example                 # VITE_API_URL
│
└── src/
    ├── main.tsx                 # ReactDOM, QueryClientProvider, Router
    ├── App.tsx                  # маршруты + защищённые страницы (нет токена → /login)
    │
    ├── api/                     # ── всё общение с backend ──
    │   ├── client.ts            # fetch-обёртка: baseURL, токен в заголовке, ошибки
    │   ├── auth.ts              # getUsers, login, me
    │   ├── chat.ts              # createSession, listSessions, getSession, ask
    │   ├── stream.ts            # askStream(question, {onStep, onResult, onError}) через fetch-event-source
    │   ├── documents.ts         # listDocuments, getDocument, getVersions
    │   ├── analytics.ts         # summary, failed, topics, gaps, timeline
    │   └── feedback.ts
    │
    ├── types/
    │   └── api.ts               # TS-копия контрактов: Document, Chunk, Citation, AgentStep, AgentResult…
    │
    ├── store/                   # zustand — глобальное состояние
    │   ├── authStore.ts         # token, user, logout, switchUser
    │   └── uiStore.ts           # открыта ли панель документа, какой doc_id/section подсветить
    │
    ├── hooks/
    │   ├── useAuth.ts
    │   ├── useChatStream.ts     # стейт текущего ответа: steps[], result, isStreaming, error
    │   └── useDocument.ts
    │
    ├── pages/
    │   ├── LoginPage.tsx
    │   ├── ChatPage.tsx
    │   ├── DocumentsPage.tsx
    │   ├── DocumentViewPage.tsx
    │   └── AnalyticsPage.tsx
    │
    ├── components/
    │   ├── layout/
    │   │   ├── AppShell.tsx         # шапка + контент
    │   │   ├── Header.tsx           # лого, модель, навигация
    │   │   └── UserSwitcher.tsx     # выпадашка: сменить мок-пользователя
    │   │
    │   ├── chat/
    │   │   ├── SessionSidebar.tsx   # список чатов + «Новый чат»
    │   │   ├── MessageList.tsx      # автоскролл
    │   │   ├── UserMessage.tsx
    │   │   ├── AssistantMessage.tsx # выбирает карточку по status
    │   │   ├── AnswerCard.tsx       # markdown + [n] кликабельны
    │   │   ├── ConflictCard.tsx     # два источника бок о бок
    │   │   ├── RefusalCard.tsx      # причина + что делать
    │   │   ├── StatusBadge.tsx
    │   │   ├── CitationList.tsx
    │   │   ├── CitationChip.tsx     # «Регламент…, п. 3.2.1 · ред. 3 ✓» → открывает панель
    │   │   ├── AgentStepsTimeline.tsx # живой список шагов (во время стрима — с анимацией)
    │   │   ├── FeedbackButtons.tsx
    │   │   ├── ChatInput.tsx        # textarea, Enter — отправить, Shift+Enter — перенос
    │   │   └── ExampleQuestions.tsx # 4–6 кнопок-примеров на пустом экране (для демо!)
    │   │
    │   ├── documents/
    │   │   ├── DocumentTable.tsx
    │   │   ├── DocStatusBadge.tsx   # действует / заменён / отменён
    │   │   ├── DocumentViewer.tsx   # текст по пунктам, подсветка + скролл к нужному section
    │   │   ├── SectionToc.tsx       # оглавление
    │   │   ├── VersionHistory.tsx   # цепочка редакций
    │   │   └── DocumentSheet.tsx    # выезжающая панель в чате (shadcn Sheet)
    │   │
    │   ├── analytics/
    │   │   ├── KpiTiles.tsx         # всего запросов, % ответов, % отказов, 👍 %, ср. время, сэкономлено часов
    │   │   ├── QueriesTimelineChart.tsx
    │   │   ├── StatusPieChart.tsx
    │   │   ├── FailedQueriesTable.tsx  # вопрос, причина, top_score, дата
    │   │   ├── GapsList.tsx         # «чего нет в базе»
    │   │   └── TopicsChart.tsx
    │   │
    │   └── ui/                      # сгенерированные shadcn-компоненты (button, card, sheet, tabs, badge, table…)
    │
    └── lib/
        ├── format.ts                # даты, мс → «12.4 с»
        └── citations.ts             # парсинг [n] в тексте → кликабельные ссылки
```

### Мелочи, которые сильно улучшают демо

- Пустой экран чата с **примерами вопросов** (кнопки) — на видео не надо печатать.
- Шаги агента появляются **по одному с анимацией** — жюри видит многошаговость.
- Кнопка «Скопировать ответ с источниками».
- KPI-плитка **«Сэкономлено часов»** = кол-во отвеченных × 45 мин (средняя оценка из бизнес-проблемы) — бьёт прямо в задачу партнёра.
- Тёмная/светлая тема — не обязательно, только если останется время.

---

## 7. Deploy: docker-compose и офлайн

```
deploy/
├── README.md                 # инструкция: с нуля до работающего демо
├── docker-compose.yml        # основной (с GPU)
├── docker-compose.cpu.yml    # override без GPU (медленно, но работает)
├── Makefile → в корне репо
└── scripts/
    ├── download_models.sh    # ollama pull + hf download в ./models (нужен интернет ОДИН раз)
    ├── save_images.sh        # docker save всех образов в .tar — перенос на машину без интернета
    ├── load_images.sh
    └── offline_check.sh      # отключить сеть контейнерам и прогнать 3 вопроса
```

### Черновик `docker-compose.yml`

```yaml
services:
  qdrant:                      # 📄 разработчик 2 отвечает за настройки
    image: qdrant/qdrant:latest
    ports: ["6333:6333"]
    volumes: ["./data/qdrant:/qdrant/storage"]

  llm:                         # 🤖 разработчик 4 отвечает за настройки (ollama или vllm)
    image: ollama/ollama:latest
    ports: ["11434:11434"]
    volumes: ["./models/ollama:/root/.ollama"]
    deploy:
      resources:
        reservations:
          devices: [{ driver: nvidia, count: all, capabilities: [gpu] }]

  backend:                     # 🧭
    build: { context: ., dockerfile: backend/Dockerfile }
    env_file: .env
    environment:
      QDRANT_URL: http://qdrant:6333
      LLM_BASE_URL: http://llm:11434/v1
      HF_HUB_OFFLINE: "1"      # не лезть в интернет за моделями
    volumes:
      - ./models/hf:/models/hf  # bge-m3, реранкер
      - ./data:/app/data
    depends_on: [qdrant, llm]
    ports: ["8000:8000"]

  frontend:                    # 🧭
    build: { context: frontend }
    depends_on: [backend]
    ports: ["8080:80"]

networks:
  default:
    # на демо можно включить internal: true — полная изоляция от интернета
    # (тогда порты наружу не пробросятся — открывать UI через отдельную сеть/прокси)
```

> ⚠️ `build.context` для backend — **корень репо**, потому что backend импортирует `core/`, `agent/`, `retrieval/`.

### Makefile (в корне)

```makefile
up:        ; docker compose -f deploy/docker-compose.yml up -d --build
down:      ; docker compose -f deploy/docker-compose.yml down
logs:      ; docker compose -f deploy/docker-compose.yml logs -f backend
models:    ; bash deploy/scripts/download_models.sh
ingest:    ; docker compose -f deploy/docker-compose.yml exec backend python -m ingest.run
eval:      ; uv run python -m eval.run --dataset eval/datasets/test.jsonl
dev-back:  ; cd backend && uv run uvicorn app.main:app --reload --port 8000
dev-front: ; cd frontend && pnpm dev
```

---

## 8. Монорепо: как собрать

### Python: uv workspace

Один `uv.lock` на весь репо, каждый модуль — свой пакет.

```toml
# pyproject.toml (корень)
[project]
name = "keshaking"
version = "0.1.0"
requires-python = ">=3.12"

[tool.uv.workspace]
members = ["core", "ingest", "retrieval", "agent", "eval", "analytics", "backend"]

[tool.ruff]
line-length = 100
```

Каждый модуль: `core/pyproject.toml`, `retrieval/pyproject.toml` и т.д. Зависимости между ними:

```toml
# backend/pyproject.toml
[project]
name = "backend"
dependencies = ["fastapi", "uvicorn[standard]", "sqlmodel", "pydantic-settings",
                "sse-starlette", "core", "agent", "retrieval", "analytics"]

[tool.uv.sources]
core = { workspace = true }
agent = { workspace = true }
retrieval = { workspace = true }
analytics = { workspace = true }
```

> Если workspace покажется сложным — запасной вариант: один `pyproject.toml` в корне со всеми зависимостями, папки как обычные пакеты. Для хакатона это нормально.

### Frontend — отдельно, свой `package.json` (pnpm), в Python-workspace не входит.

### Корень репо, итог

```
keshaking/
├── README.md
├── Makefile
├── pyproject.toml          # uv workspace
├── uv.lock
├── .env.example
├── .gitignore
├── .github/
│   ├── pull_request_template.md
│   └── workflows/ci.yml    # ruff + pytest + pnpm build (опционально)
├── core/ ingest/ retrieval/ agent/ eval/ analytics/ backend/   # Python
├── frontend/                                                   # Node
├── deploy/ docs/ data/ models/ sandbox/
```

---

## 9. Бэклог на хакатон (после ТЗ)

> Переносим на GitHub Project. Приоритет: P0 — без этого нет демо, P1 — сильно влияет на оценку, P2 — если успеем.

| P | Задача | Зависит от |
|---|---|---|
| P0 | Монорепо, uv workspace, `core/schemas.py` | контракты |
| P0 | Backend: config, db, seed, health, auth | — |
| P0 | Backend: `/chat/ask/stream` на mock-агенте | — |
| P0 | Frontend: вход, чат, стрим шагов, карточка ответа, цитаты | backend mock |
| P0 | Переключение на реального агента | 🤖 |
| P0 | docker-compose: всё поднимается `make up` (👥 П4) | 📄 🤖 |
| P1 | Логи QueryLog + AgentStepLog, фидбек 👍/👎 | — |
| P1 | Документы: список, просмотрщик, подсветка пункта, история редакций | 🔎 |
| P1 | Права доступа: фильтр + понятное сообщение об отказе по правам | 📄 🔎 |
| P1 | Аналитика: API + дашборд (👥 П3) | 🔎 |
| P1 | Офлайн-проверка на чистой машине | все |
| P2 | История сессий в сайдбаре | — |
| P2 | Карточка конфликта бок о бок | 🤖 |
| P2 | CI в GitHub Actions | — |
| P2 | Экспорт ответа / копирование | — |

---

## 10. Самопроверка к 26.11

Ответь «да» на всё:

- [ ] Репо создан, все добавлены, `main` защищён, доска задач есть
- [ ] Знаю, на каком железе крутится LLM на хакатоне и на демо
- [ ] Могу за 10 минут поднять FastAPI с роутером, `Depends` и pydantic-моделями
- [ ] Умею отдавать SSE из FastAPI и читать их на фронте из POST-запроса
- [ ] Умею создать таблицу в SQLModel, записать, сделать агрегат (count/group by)
- [ ] Могу создать Vite + React + TS + Tailwind + shadcn проект за 15 минут
- [ ] Есть макеты 4 экранов
- [ ] `docker compose up` поднимает хотя бы qdrant + ollama + игрушечный backend
- [ ] Понимаю каждое поле `AgentResult` и как его показать
- [ ] Решено: React или Streamlit

---

## 11. Риски и подводные камни

| Риск | Что делать |
|---|---|
| **nginx буферизует SSE** → шаги приходят пачкой в конце | В `location /api/` поставить `proxy_buffering off;`, `proxy_cache off;`, `proxy_read_timeout 300s;`, а в ответе — заголовок `X-Accel-Buffering: no` |
| Агент синхронный и блокирует event loop | Запускать через `run_in_threadpool` / `asyncio.to_thread`, для стрима — итератор в отдельном потоке + `asyncio.Queue` |
| Ответ LLM долгий (30+ с) → кажется, что всё зависло | Стрим шагов + индикатор «агент думает», таймаут с понятной ошибкой |
| CORS-ошибки в dev | Vite proxy `/api` → backend, тогда CORS не нужен вообще |
| Капитан утонул в координации и не пишет код | Синки строго 15 мин; на хакатоне первые 2 дня — фокус на P0 |
| Модели скачиваются в контейнере при старте → на демо без интернета падает | Всё качаем заранее в `./models`, монтируем volume, `HF_HUB_OFFLINE=1` |
| GPU не видна в docker | Установить NVIDIA Container Toolkit, проверить `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi` |
| Фронт не успеваем | Запасной Streamlit-интерфейс за день, решение — не позже середины этапа 2 |
