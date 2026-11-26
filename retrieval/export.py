"""Выгрузка пунктов 4 DOCX в формате контракта (docs/02-contracts.md §1).

    python -m retrieval.export                  # → data/processed/chunks.jsonl, documents.jsonl
    python -m retrieval.export --out some/dir   # в другую папку

Общая выгрузка для ingest, поиска и eval. Читает исходные DOCX при каждом запуске.

⚠️ Файлы содержат текст регламентов заказчика — в git не кладём (`data/` в .gitignore).
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from shutil import copyfile
from uuid import uuid4

from retrieval import mock


@contextmanager
def _lock_output(out: Path) -> Iterator[None]:
    # The OS releases the lock when the handle closes, including after a process exit.
    with (out / ".ingest.lock").open("a+b") as lock:
        lock.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def export(out: Path, *, require_complete: bool = False) -> tuple[int, int]:
    try:
        docs, chunks = mock.load_docx_corpus(require_complete=require_complete)
    except ValueError as error:
        raise SystemExit(str(error)) from None
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
    keep_backups = False
    try:
        for filename, records in outputs:
            with (staging / filename).open("w", encoding="utf-8", newline="\n") as file:
                for record in records:
                    file.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

        with _lock_output(out):
            for filename, _ in outputs:
                target = out / filename
                if target.exists():
                    copyfile(target, staging / f"{filename}.previous")

            published = []
            try:
                for filename, _ in outputs:
                    (staging / filename).replace(out / filename)
                    published.append(filename)
            except BaseException:
                try:
                    for filename in reversed(published):
                        backup = staging / f"{filename}.previous"
                        if backup.exists():
                            backup.replace(out / filename)
                        else:
                            (out / filename).unlink()
                except OSError as error:
                    keep_backups = True
                    raise OSError(f"Не удалось восстановить выгрузку. Резервные файлы в {staging}.") from error
                raise
    finally:
        if not keep_backups:
            for filename, _ in outputs:
                (staging / filename).unlink(missing_ok=True)
                (staging / f"{filename}.previous").unlink(missing_ok=True)
            staging.rmdir()

    return len(docs), len(chunks)


def main(*, require_complete: bool = False) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=mock.PROCESSED_DIR)
    a = ap.parse_args()
    try:
        n_docs, n_chunks = export(a.out, require_complete=require_complete)
    except OSError as error:
        raise SystemExit(f"Не удалось записать выгрузку в {a.out}. {error}") from None
    print(f"{n_docs} документов, {n_chunks} пунктов → {a.out}")


if __name__ == "__main__":
    main()
