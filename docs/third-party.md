# Компоненты третьих лиц

> По условиям перечень использованных библиотек, моделей и наработок третьих лиц **обязателен** в описании продукта.
> Для моделей организаторы просят ссылку на карточку Hugging Face / страницу модели с лицензией (чат задачи, 26.11).
> Сверено с кодом 28.11 (ветки `main` и `integration/punkt-release`).

## Используется в демо-сборке «Пункт»

| Компонент | Для чего | Лицензия | Ссылка |
|---|---|---|---|
| **Qwen3-8B** (`qwen3:8b`) | LLM агента | Apache 2.0 | huggingface.co/Qwen/Qwen3-8B · ollama.com/library/qwen3 |
| **Ollama** | локальный запуск LLM | MIT | github.com/ollama/ollama |
| FastAPI | backend API | MIT | github.com/fastapi/fastapi |
| Starlette (зависимость FastAPI) | веб-слой | BSD-3-Clause | github.com/encode/starlette |
| Pydantic | схемы данных | MIT | github.com/pydantic/pydantic |
| Uvicorn | ASGI-сервер | BSD-3-Clause | github.com/encode/uvicorn |
| React, React DOM | интерфейс | MIT | github.com/facebook/react |
| Vite | сборка фронтенда | MIT | github.com/vitejs/vite |
| TypeScript | фронтенд | Apache 2.0 | github.com/microsoft/TypeScript |
| Mailpit | тестовый почтовый ящик службы качества (демо) | MIT | github.com/axllent/mailpit |
| Docker-образы `node:24-bookworm-slim`, `python:3.12-slim-bookworm` | сборка и запуск | Node.js — MIT, Python — PSF; компоненты Debian — свои лицензии | hub.docker.com |
| uv | установка Python-зависимостей в образе | MIT или Apache 2.0 | github.com/astral-sh/uv |

Своё (не сторонние компоненты): поиск BM25 с синонимами, разбор DOCX (стандартная библиотека Python: `zipfile`, `xml`), агент, проверка числовых порогов, хранение в SQLite (стандартная библиотека), eval и калькулятор стоимости.

## Опционально (гибридный поиск, включается при наличии моделей — в демо-сборку не входит)

| Компонент | Для чего | Лицензия | Ссылка |
|---|---|---|---|
| BAAI/bge-m3 | эмбеддинги | MIT | huggingface.co/BAAI/bge-m3 |
| BAAI/bge-reranker-v2-m3 | реранкер | Apache 2.0 | huggingface.co/BAAI/bge-reranker-v2-m3 |
| FlagEmbedding | инференс bge | MIT | github.com/FlagOpen/FlagEmbedding |
| PyTorch | инференс моделей | BSD-3-Clause | pytorch.org |
| NumPy | векторы | BSD-3-Clause | numpy.org |

## Рассматривались, но не используются

LangGraph, Qdrant, docling, python-docx, PyMuPDF, rank-bm25, razdel, pymorphy3, vLLM — в описание продукта **не** включать.

## Дизайн (заполнить, когда выберем)

| Компонент | Лицензия |
|---|---|
| Шрифты (например, Unbounded, Golos Text — Google Fonts) | SIL Open Font License 1.1 |
| Набор иконок | (Phosphor — MIT; Solar — CC BY 4.0, указать автора) |
