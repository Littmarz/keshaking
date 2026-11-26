# ingest/ — документы → чанки с метаданными

**Владелец:** 📄 Разработчик 2 — Документы · **Подробный план:** [../docs/team/2-documents.md](../docs/team/2-documents.md)

`data/raw/*.docx` (4 регламента заказчика, не в git) → python-docx → дерево пунктов → чанки (1 чанк = 1 пункт со всеми подпунктами и условиями) → `doc_id`, `section`, **дирекция, этап** → `data/processed/chunks.jsonl`

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
