# 👥 Общий чек-лист для всех (Сб 21.11, ~2 часа)

> Сделай до вечера субботы и отметь в чате «готово ✅».

## 1. Аккаунты

- [ ] **GitHub** — скинь ник капитану, он добавит в репо
- [ ] **Hugging Face** (huggingface.co) — нужен для скачивания моделей. Создай токен: Settings → Access Tokens → Read
- [ ] Вступи в общий чат команды

## 2. Установить

| Что | Зачем | Проверка |
|---|---|---|
| **Git** | репозиторий | `git --version` |
| **Python 3.12** | весь бэкенд и ML | `python --version` |
| **uv** (менеджер пакетов Python, быстрее pip) | окружения, зависимости | `uv --version` |
| **Docker Desktop** (Windows/Mac) или Docker Engine + compose plugin (Linux) | Qdrant, сборка | `docker compose version` |
| **VS Code** | редактор | — |
| **Ollama** (ollama.com) | локальные LLM | `ollama --version` |

Установка uv:
```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**Windows:** ставь **WSL2 + Ubuntu** и работай внутри неё — с Docker, CUDA и Python будет сильно меньше проблем.

**Есть NVIDIA GPU?** Проверь `nvidia-smi` — должна показаться карта и версия драйвера. Запиши модель карты и VRAM в [README.md](README.md).

## 3. Расширения VS Code

- Python, Pylance (Microsoft)
- Ruff (Astral)
- Docker
- GitLens
- Markdown Preview Mermaid Support
- Even Better TOML
- 🧭 капитану ещё: ESLint, Prettier, Tailwind CSS IntelliSense, Thunder Client (или REST Client)

## 4. Проверь, что всё работает (10 минут)

```bash
# Python + uv (без своего кода — только проверка установки)
uv --version && uv python list

# Docker
docker run --rm hello-world

# Ollama (маленькая модель для проверки, ~0.5 ГБ)
ollama pull qwen3:0.6b
ollama run qwen3:0.6b "Привет! Ответь одним словом."
```

## 5. Прочитать (30 минут)

- [ ] ⛔ **Правило: до 10:00 26.11 код не пишем** — [../00-conditions.md](../00-conditions.md)

- [ ] [../../README.md](../../README.md) — общий обзор
- [ ] [../00-conditions.md](../00-conditions.md) — условия
- [ ] [../01-architecture.md](../01-architecture.md) — архитектура
- [ ] [../02-contracts.md](../02-contracts.md) — контракты (хотя бы свою часть)
- [ ] Свой файл в `docs/team/`

## 6. Общие понятия, которые должен понимать каждый (1 час)

| Термин | Суть в одной строке |
|---|---|
| **RAG** | Сначала найти релевантные куски документов, потом дать их LLM и попросить ответить только по ним |
| **Чанк** | Кусок документа, который индексируем и находим (у нас — один пункт регламента) |
| **Эмбеддинг** | Вектор чисел, отражающий смысл текста; похожие тексты → близкие векторы |
| **BM25** | Классический поиск по словам (как поисковик), хорош для номеров и терминов |
| **Гибридный поиск** | BM25 + векторный, результаты объединяются |
| **Реранкер** | Вторая модель, которая точнее пересортировывает топ найденного |
| **Агент** | LLM, которая сама решает, какие шаги делать (искать ещё, переформулировать, проверить), а не один вызов |
| **Галлюцинация** | LLM уверенно придумала то, чего нет в документах — главный враг |
| **Grounding / цитаты** | Каждое утверждение ответа подкреплено конкретным местом в документе |

Видео на вечер (любое по запросу «RAG explained» / «что такое RAG» на YouTube, 15–20 минут) — чтобы у всей команды была одна картинка в голове.
