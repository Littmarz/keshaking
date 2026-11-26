import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

from retrieval import mock
from retrieval.docx_sections import parse_sections, read_blocks
from retrieval.export import export


_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_SAMPLE_PARAGRAPHS = """
<w:p><w:r><w:t>Test policy</w:t></w:r></w:p>
<w:p><w:r><w:t>1. Documents</w:t></w:r></w:p>
<w:p><w:r><w:t>1.1. Example clause</w:t></w:r></w:p>
"""


def write_docx(path: Path, paragraphs: str = _SAMPLE_PARAGRAPHS, *, numbering: str | None = None) -> None:
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="{_WORD_NS}"><w:body>{paragraphs}</w:body></w:document>',
        )
        if numbering is not None:
            archive.writestr("word/numbering.xml", f'<w:numbering xmlns:w="{_WORD_NS}">{numbering}</w:numbering>')


def paragraphs(*lines: str) -> str:
    return "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in lines)


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

    def test_export_keeps_clauses_without_a_separate_section_heading(self) -> None:
        write_docx(
            self.raw / "ib-project.docx",
            paragraphs("Test policy", "Preface", "1.1. First clause", "1.2. Second clause", "2.1. Next section"),
        )

        export(self.out)

        rows = (self.out / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            [row["chunk_id"] for row in map(json.loads, rows)],
            ["ib-project::0", "ib-project::1.1", "ib-project::1.2", "ib-project::2.1"],
        )

    def test_numbered_first_paragraph_remains_a_clause(self) -> None:
        source = self.raw / "ib-project.docx"
        write_docx(source, paragraphs("1.1. First clause", "1.2. Second clause"))

        title, sections = parse_sections(source)

        self.assertEqual(title, source.stem)
        self.assertEqual([section.section for section in sections], ["1.1", "1.2"])

    def test_flat_numbered_sections_have_separate_ids(self) -> None:
        source = self.raw / "ib-project.docx"
        write_docx(source, paragraphs("Test policy", "1. First clause", "2. Second clause", "3. Third clause"))

        _, sections = parse_sections(source)

        self.assertEqual([section.section for section in sections], ["1", "2", "3"])

    def test_nested_numbered_list_stays_in_the_final_clause(self) -> None:
        source = self.raw / "ib-project.docx"
        write_docx(source, paragraphs("Test policy", "1. Heading", "1.1. Clause", "1. First item", "2. Second item"))

        _, sections = parse_sections(source)

        self.assertEqual([section.section for section in sections], ["1", "1.1"])
        self.assertIn("2. Second item", sections[-1].text)

    def test_table_cell_paragraphs_are_separated(self) -> None:
        source = self.raw / "ib-project.docx"
        table = (
            "<w:tbl><w:tr><w:tc>" + paragraphs("First paragraph", "Second paragraph")
            + "</w:tc><w:tc>" + paragraphs("Right column") + "</w:tc></w:tr></w:tbl>"
        )
        write_docx(source, paragraphs("Test policy", "1. Heading", "1.1. Documents") + table)

        _, sections = parse_sections(source)

        self.assertIn("| First paragraph Second paragraph | Right column |", sections[-1].text)

    def test_word_lists_use_independent_counters_and_numbering_overrides(self) -> None:
        paragraphs = "".join(
            f'<w:p><w:pPr><w:numPr><w:numId w:val="{num_id}"/></w:numPr></w:pPr>'
            f'<w:r><w:t>{text}</w:t></w:r></w:p>'
            for num_id, text in (("4", "A1"), ("5", "B1"), ("4", "A2"), ("5", "B2"), ("6", "C1"), ("6", "C2"))
        )
        numbering = """
<w:abstractNum w:abstractNumId="0">
  <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>
</w:abstractNum>
<w:num w:numId="4"><w:abstractNumId w:val="0"/></w:num>
<w:num w:numId="5"><w:abstractNumId w:val="0"/></w:num>
<w:num w:numId="6"><w:abstractNumId w:val="0"/>
  <w:lvlOverride w:ilvl="0">
    <w:startOverride w:val="7"/>
    <w:lvl w:ilvl="0"><w:start w:val="2"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1)"/></w:lvl>
  </w:lvlOverride>
</w:num>
"""
        source = self.raw / "ib-project.docx"
        write_docx(source, paragraphs, numbering=numbering)

        self.assertEqual(read_blocks(source), ["1. A1", "1. B1", "2. A2", "2. B2", "7) C1", "8) C2"])

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
