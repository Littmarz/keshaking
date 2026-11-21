# retrieval/ — гибридный поиск

**Владельцы:** 🔎 Разработчик 3 — поиск ([план](../docs/team/3-search-quality.md)) · 📄 Разработчик 2 — построение индекса: `index.py`, `embedder.py`, `bm25_index.py`, запись в `qdrant_store.py` ([план](../docs/team/2-documents.md)) · **Контракт:** [../docs/02-contracts.md §2](../docs/02-contracts.md)

BM25 (леммы) + bge-m3 (Qdrant) → RRF → bge-reranker-v2-m3 → фильтры по статусу и правам.

```
retrieval/
├── search.py          ⭐ search() · get_versions() · get_section() · list_documents()
├── index.py  embedder.py  bm25_index.py   📄 построение индекса
├── qdrant_store.py    общий: 📄 пишет, 🔎 читает
├── query.py  synonyms.py  dense.py  lexical.py  hybrid.py  reranker.py  filters.py   🔎
├── eval_retrieval.py  Recall@k, MRR
├── mock.py            заглушка для 🤖 в первые часы
└── tests/
```
