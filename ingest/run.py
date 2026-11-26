"""Выгрузка полного корпуса регламентов."""

from retrieval.export import export
from retrieval.mock import DOC_META, PROCESSED_DIR, RAW_DIR


def main() -> None:
    missing = sorted(f"{doc_id}.docx" for doc_id in DOC_META
                     if not (RAW_DIR / f"{doc_id}.docx").is_file())
    if missing:
        raise SystemExit(f"Не найдены DOCX в {RAW_DIR}: {', '.join(missing)}")

    documents, chunks = export(PROCESSED_DIR)
    print(f"{documents} документов, {chunks} пунктов → {PROCESSED_DIR}")


if __name__ == "__main__":
    main()
