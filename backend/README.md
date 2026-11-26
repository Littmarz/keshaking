# backend/ — FastAPI

**Владелец:** 🧭 Капитан · **Подробный план:** [../docs/team/1-captain-backend-frontend.md §5](../docs/team/1-captain-backend-frontend.md#5-backend-структура-и-назначение-каждого-файла)

API для фронтенда: мок-авторизация и права, чат со стримом шагов агента (SSE), документы, логи, фидбек, аналитика.

```
backend/
├── app/
│   ├── main.py  config.py  deps.py  security.py
│   ├── api/        health · auth · chat · feedback · documents · analytics · admin
│   ├── schemas/    DTO запросов/ответов
│   ├── db/         session · models · seed
│   ├── services/   agent · chat · log · access · document · analytics
│   ├── mocks/      fake_agent.py  ← работаем без агента: AGENT_MODE=mock
│   └── utils/      sse · timing
└── tests/
```

## Запуск (после 26.11)

```bash
cp .env.example .env
uv run uvicorn app.main:app --reload --port 8000
# http://localhost:8000/docs
```

### Локальная Qwen

Запустите Ollama на Mac и проверьте наличие `qwen3:8b` командой `ollama list`.
Из папки `backend/` запустите API в режиме модели:

```bash
AGENT_MODE=real uv run --python 3.12 uvicorn app.main:app --port 8000
```

`LLM_BASE_URL` по умолчанию указывает на `http://127.0.0.1:11434`, а
`LLM_MODEL` — на `qwen3:8b`. Агент вызывает нативный `/api/chat` Ollama
и добавляет счётчики токенов в `steps[].detail`. Сейчас источники — учебные
пункты и подтверждённые записи службы качества, доступные демо.
