"""Выгрузка пунктов 4 DOCX в формате контракта (docs/02-contracts.md §1).

    python -m retrieval.export                  # → data/processed/chunks.jsonl, documents.jsonl
    python -m retrieval.export --out some/dir   # в другую папку

Источник — временный разбор `retrieval/docx_sections.py` (те же пункты, что видит `retrieval.mock`).
Нужен, чтобы ingest (разработчик 2) и агент (разработчик 4) работали на тех же данных, что поиск и eval.
Когда ingest начнёт выдавать свой `chunks.jsonl`, эта команда больше не нужна: `retrieval.mock`
сам читает `data/processed/chunks.jsonl`, если он есть.

⚠️ Файлы содержат текст регламентов заказчика — в git не кладём (`data/` в .gitignore).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from retrieval import mock


def export(out: Path) -> tuple[int, int]:
    docs, chunks = mock._chunks_from_docx()  # всегда из DOCX, даже если chunks.jsonl уже лежит
    if not docs:
        raise SystemExit(f"Нет DOCX в {mock.RAW_DIR} (ожидаются ib-project, ib-presale, dbrct-project, tech-project).")
    out.mkdir(parents=True, exist_ok=True)
    (out / "documents.jsonl").write_text(
        "".join(json.dumps(d.to_dict(), ensure_ascii=False) + "\n" for d in docs), encoding="utf-8")
    (out / "chunks.jsonl").write_text(
        "".join(json.dumps(c.to_dict(), ensure_ascii=False) + "\n" for c in chunks), encoding="utf-8")
    return len(docs), len(chunks)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=mock.PROCESSED_DIR)
    a = ap.parse_args()
    n_docs, n_chunks = export(a.out)
    print(f"{n_docs} документов, {n_chunks} пунктов → {a.out}")


if __name__ == "__main__":
    main()
