# Компоненты третьих лиц

> По условиям перечень использованных библиотек, моделей и наработок третьих лиц **обязателен** в описании продукта.
> Вписываем сюда только то, что **реально попало в решение**. Лицензию сверяем с карточкой/репозиторием в момент добавления.

| Компонент | Для чего | Лицензия | Используем? |
|---|---|---|---|
| Qwen3-8B (`qwen3:8b`, D-004) | LLM | Apache 2.0 | да |
| BAAI/bge-m3 | эмбеддинги | MIT | |
| BAAI/bge-reranker-v2-m3 | реранкер | Apache 2.0 | |
| Ollama | запуск LLM | MIT | да |
| Qdrant | векторная БД | Apache 2.0 | |
| LangGraph | граф агента | MIT | |
| FastAPI, SQLModel | backend | MIT | |
| docling | парсинг PDF/DOCX | MIT | |
| python-docx | парсинг DOCX | MIT | |
| PyMuPDF | запасной парсер PDF | ⚠️ **AGPL-3.0** — при закрытом коде лучше не брать или осознанно указать | |
| rank-bm25 | лексический поиск | Apache 2.0 | |
| razdel, pymorphy3 | токенизация, леммы | MIT | |
| FlagEmbedding | инференс bge | MIT | |
| React, Vite, Tailwind, shadcn/ui, Recharts | frontend | MIT | |

## Сборка демо через Docker Compose

| Компонент | Для чего | Лицензия |
|---|---|---|
| [Node.js](https://github.com/nodejs/node/blob/main/LICENSE) 24, образ `node:24-bookworm-slim` | сборка React и Vite | MIT для Node.js |
| [Python](https://docs.python.org/3/license.html) 3.12, образ `python:3.12-slim-bookworm` | среда FastAPI | PSF для Python |
| [uv](https://github.com/astral-sh/uv/blob/main/docs/reference/policies/license.md) 0.13.10 | установка зависимостей Python в контейнере | MIT или Apache 2.0 |

Компоненты Debian и сторонние зависимости в базовых образах распространяются под собственными лицензиями.
