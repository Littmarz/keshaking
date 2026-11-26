# deploy/ — стенд

**Владелец:** 🧭 Капитан · **Подробно:** [../docs/team/1-captain-backend-frontend.md §7](../docs/team/1-captain-backend-frontend.md#7-deploy-docker-compose-и-офлайн)

Стенд нужен **по URL до 03.12 23:59 МСК**; по запросу до итогов — поднять за 24 ч.

| Компонент | Где |
|---|---|
| Ollama + `qwen3:8b` | **нативно на Mac 24 ГБ** (Metal). В docker на Mac GPU недоступен |
| backend, frontend, qdrant | docker-compose; backend ходит к LLM по `http://host.docker.internal:11434/v1` |
| Доступ снаружи | туннель с HTTPS или VPS; проверять с телефона вне домашней сети |

```
deploy/
├── docker-compose.yml        # qdrant + backend + frontend (без GPU-сервиса LLM)
└── scripts/
    ├── download_models.sh    # ollama pull qwen3:8b + bge-m3 в ./models (интернет один раз)
    └── smoke.sh              # health + 3 вопроса из eval/datasets/control.jsonl
```

```bash
make models
make up        # все compose-команды с --project-directory .
make ingest    # 4 DOCX из data/raw/
```
