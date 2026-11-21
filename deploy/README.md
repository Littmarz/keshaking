# deploy/ — docker-compose и офлайн-сборка

**Владелец:** 🧭 Капитан (сервисы `qdrant` — 📄, `llm` — 🤖) · **Подробно:** [../docs/team/1-captain-backend-frontend.md §7](../docs/team/1-captain-backend-frontend.md#7-deploy-docker-compose-и-офлайн)

```
deploy/
├── docker-compose.yml        # qdrant + llm + backend + frontend (GPU)
├── docker-compose.cpu.yml    # без GPU
└── scripts/
    ├── download_models.sh    # всё скачать заранее (интернет нужен один раз)
    ├── save_images.sh        # docker save → .tar
    ├── load_images.sh
    └── offline_check.sh      # прогон без сети
```

## Цель

```bash
make models   # один раз, с интернетом
make up       # дальше — без интернета
make ingest
# UI: http://localhost:8080
```
