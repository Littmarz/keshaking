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
