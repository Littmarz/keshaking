# retrieval/ — гибридный поиск

**Владельцы:** 🔎 Разработчик 3 — поиск ([план](../docs/team/3-search-quality.md)) · 📄 Разработчик 2 — построение индекса: `index.py`, `embedder.py`, `bm25_index.py`, запись в `qdrant_store.py` ([план](../docs/team/2-documents.md)) · **Контракт:** [../docs/02-contracts.md §2](../docs/02-contracts.md)

BM25 (леммы) + bge-m3 (Qdrant) → RRF → bge-reranker-v2-m3 → фильтры по статусу и правам.

## ✅ Уже есть: заглушка `retrieval/mock.py` (26.11)

Рабочий `search()` по контракту ([§2](../docs/02-contracts.md)) **без зависимостей** (только стандартная библиотека Python ≥ 3.10):

```python
from retrieval.mock import search, get_section, upsert_qa_entry
res = search("Кто назначает проджект менеджера на проекты ИБ?", k=5)
for h in res.hits:
    print(h.score, h.chunk.chunk_id, h.chunk.text[:80])   # 0.77 ib-project::2.1 …
res.to_dict()   # JSON для SSE / логов
```

- Пункты: `data/processed/chunks.jsonl` (когда его отдаст ingest), иначе временный разбор 4 DOCX из `data/raw/` (`docx_sections.py`; файлы называются `ib-project.docx`, `ib-presale.docx`, `dbrct-project.docx`, `tech-project.docx`).
- BM25 + лёгкий стеммер + синонимы из [`synonyms.json`](synonyms.json) (глоссарий заказчика).
- Дирекция в вопросе («ИБ», «ДБРЦТ», «Техдирекция») — **буст области**, а не слова поиска; пункты-заголовки понижены.
- Записи службы качества: `upsert_qa_entry({..., "status": "confirmed"})` → `data/processed/qa_entries.jsonl` → ищутся с `source_type = "qa_entry"`. Черновики не попадают.
- Отладка: `python -m retrieval.mock "ваш вопрос"`; тесты: `python -m unittest discover -s retrieval/tests -t .`
- Качество на контрольном наборе: см. [../eval/reports/](../eval/reports/) (на 26.11: hit@5 = 0.93, MRR = 0.80).

Типы — dataclass с полями контракта. Когда появится `core/schemas.py`, переключим импорт: поля совпадают.

```
retrieval/
├── search.py          ⭐ search() · get_versions() · get_section() · list_documents()
├── index.py  embedder.py  bm25_index.py   📄 построение индекса
├── qdrant_store.py    общий: 📄 пишет, 🔎 читает
├── query.py  synonyms.py  dense.py  lexical.py  hybrid.py  reranker.py  filters.py   🔎
├── eval_retrieval.py  Recall@k, MRR
├── mock.py            ✅ заглушка по контракту (BM25 + синонимы + записи качества)
├── docx_sections.py   ✅ временный разбор DOCX (до chunks.jsonl от ingest)
├── synonyms.json      ✅ глоссарий заказчика → синонимы
└── tests/
```
