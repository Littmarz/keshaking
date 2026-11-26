"""Выгрузка пунктов 4 DOCX в формате контракта (docs/02-contracts.md §1).

    python -m retrieval.export                  # → data/processed/chunks.jsonl, documents.jsonl
    python -m retrieval.export --out some/dir   # в другую папку

Общая выгрузка для ingest, поиска и eval. Читает исходные DOCX при каждом запуске.

⚠️ Файлы содержат текст регламентов заказчика — в git не кладём (`data/` в .gitignore).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4

from retrieval import mock


def export(out: Path) -> tuple[int, int]:
    docs, chunks = mock.load_docx_corpus()
    if not docs:
        raise SystemExit(f"Нет DOCX в {mock.RAW_DIR} (ожидаются ib-project, ib-presale, dbrct-project, tech-project).")
    chunk_doc_ids = {chunk.doc_id for chunk in chunks}
    empty = sorted(doc.doc_id for doc in docs if doc.doc_id not in chunk_doc_ids)
    if empty:
        raise SystemExit(f"Нет пунктов в DOCX: {', '.join(empty)}")

    out.mkdir(parents=True, exist_ok=True)
    outputs = (("documents.jsonl", docs), ("chunks.jsonl", chunks))
    staging = out / f".ingest-{uuid4().hex}"
    # Windows files keep their inherited permissions when moved out of staging.
    staging.mkdir()
    try:
        for filename, records in outputs:
            with (staging / filename).open("w", encoding="utf-8", newline="\n") as file:
                for record in records:
                    file.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

        for filename, _ in outputs:
            (staging / filename).replace(out / filename)
    finally:
        for filename, _ in outputs:
            (staging / filename).unlink(missing_ok=True)
        staging.rmdir()

    return len(docs), len(chunks)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=mock.PROCESSED_DIR)
    a = ap.parse_args()
    n_docs, n_chunks = export(a.out)
    print(f"{n_docs} документов, {n_chunks} пунктов → {a.out}")


if __name__ == "__main__":
    main()
