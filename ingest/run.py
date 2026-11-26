"""Export the DOCX corpus as JSONL for retrieval."""

import json
from uuid import uuid4

from retrieval.mock import DOC_META, PROCESSED_DIR, RAW_DIR, load_docx_corpus


def main() -> None:
    missing = sorted(f"{doc_id}.docx" for doc_id in DOC_META
                     if not (RAW_DIR / f"{doc_id}.docx").is_file())
    if missing:
        raise SystemExit(f"Missing DOCX in {RAW_DIR}: {', '.join(missing)}")

    documents, chunks = load_docx_corpus()
    chunk_doc_ids = {chunk.doc_id for chunk in chunks}
    empty = sorted(doc.doc_id for doc in documents if doc.doc_id not in chunk_doc_ids)
    if empty:
        raise SystemExit(f"No sections extracted from: {', '.join(empty)}")

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    outputs = (("documents.jsonl", documents), ("chunks.jsonl", chunks))
    staging = PROCESSED_DIR / f".ingest-{uuid4().hex}"
    # Windows files keep their inherited permissions when moved out of staging.
    staging.mkdir()
    try:
        for filename, records in outputs:
            with (staging / filename).open("w", encoding="utf-8", newline="\n") as file:
                for record in records:
                    file.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

        for filename, _ in outputs:
            (staging / filename).replace(PROCESSED_DIR / filename)
    finally:
        for filename, _ in outputs:
            (staging / filename).unlink(missing_ok=True)
        staging.rmdir()

    print(f"Exported {len(documents)} documents and {len(chunks)} chunks to {PROCESSED_DIR}")


if __name__ == "__main__":
    main()
