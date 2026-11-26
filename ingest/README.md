# ingest/ — документы → чанки с метаданными

**Владелец:** 📄 Разработчик 2 — Документы · **Подробный план:** [../docs/team/2-documents.md](../docs/team/2-documents.md)

Положи `ib-project.docx`, `ib-presale.docx`, `dbrct-project.docx` и `tech-project.docx` в `data/raw/`. Из корня репозитория запусти `python -m ingest.run` на Python 3.12. Команда использует стандартную библиотеку Python.

В `data/processed/` появятся `documents.jsonl` с метаданными и `chunks.jsonl` с пунктами. Поиск подхватит выгрузку при следующем запуске. На демо достаточно подключить эту папку через том `data` в Compose.

Пути можно задать через `RAW_DIR` и `PROCESSED_DIR`. Каталог `data/` игнорируется Git.

```
ingest/
├── run.py         точка входа
├── loaders/       pdf · docx · xlsx · txt/md
├── cleaning.py    колонтитулы, переносы
├── structure.py   текст → разделы/пункты
├── chunker.py     пункты → Chunk
├── metadata.py    тип, номер, даты, редакция, ссылки
├── versioning.py  ⭐ действующая редакция
├── access.py      уровень доступа документа
└── tests/
```
