import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

from retrieval import mock
from retrieval.export import export


def write_docx(path: Path) -> None:
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", """
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Test policy</w:t></w:r></w:p>
    <w:p><w:r><w:t>1. Documents</w:t></w:r></w:p>
    <w:p><w:r><w:t>1.1. Example clause</w:t></w:r></w:p>
  </w:body>
</w:document>
""")


class TestCorpusExport(unittest.TestCase):
    def setUp(self) -> None:
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        self.raw = root / "raw"
        self.raw.mkdir()
        self.out = root / "processed"
        source = patch.object(mock, "RAW_DIR", self.raw)
        source.start()
        self.addCleanup(source.stop)

    def test_complete_export_uses_canonical_ids_and_skips_non_documents(self) -> None:
        for doc_id in mock.DOC_META:
            write_docx(self.raw / f"{doc_id.upper()}.DOCX")
        (self.raw / "~$ib-project.docx").write_bytes(b"Word owner file")
        (self.raw / "notes.docx").mkdir()

        self.assertEqual(export(self.out, require_complete=True), (4, 8))
        rows = (self.out / "documents.jsonl").read_text(encoding="utf-8").splitlines()
        directorates = {row["doc_id"]: row["directorate"] for row in map(json.loads, rows)}
        self.assertEqual(directorates, {doc_id: meta["directorate"] for doc_id, meta in mock.DOC_META.items()})

    def test_invalid_input_error_identifies_the_document(self) -> None:
        (self.raw / "ib-project.docx").write_bytes(b"Not a DOCX")

        with self.assertRaisesRegex(SystemExit, r"ib-project\.docx"):
            export(self.out)

    def test_failed_replacement_restores_the_previous_export(self) -> None:
        write_docx(self.raw / "ib-presale.docx")
        export(self.out)
        destinations = [self.out / filename for filename in ("documents.jsonl", "chunks.jsonl")]
        original = [path.read_bytes() for path in destinations]
        write_docx(self.raw / "ib-project.docx")

        replace_path = Path.replace

        def refuse_chunks(source: Path, target: Path) -> Path:
            if target == destinations[1]:
                raise PermissionError("Chunks are open in another process")
            return replace_path(source, target)

        with patch.object(Path, "replace", refuse_chunks), self.assertRaises(PermissionError):
            export(self.out)

        self.assertEqual([path.read_bytes() for path in destinations], original)
