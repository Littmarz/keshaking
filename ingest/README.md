# ingest/ — документы → чанки с метаданными

**Владелец:** 📄 Разработчик 2 — Документы · **Подробный план:** [../docs/team/2-documents.md](../docs/team/2-documents.md)

`data/raw/*` → парсинг → очистка → дерево пунктов → чанки (1 чанк = 1 пункт) → метаданные → **статус редакции** → `data/processed/chunks.jsonl`

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
