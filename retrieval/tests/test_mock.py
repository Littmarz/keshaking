"""python -m unittest discover -s retrieval/tests -t .   (нужны 4 DOCX в data/raw/)"""

import json
import tempfile
import unittest
from pathlib import Path

from retrieval import mock
from retrieval.docx_sections import parse_sections

RAW = mock.RAW_DIR
HAVE_DATA = all((RAW / f"{d}.docx").exists() for d in mock.DOC_META)


@unittest.skipUnless(HAVE_DATA, "нет DOCX в data/raw/")
class TestParsing(unittest.TestCase):
    def test_nested_numbering_stays_inside_1_3(self):
        _, secs = parse_sections(RAW / "ib-presale.docx")
        tops = [s.section for s in secs if "." not in s.section]
        self.assertEqual(tops, [str(i) for i in range(1, len(tops) + 1)])  # 1..N без повторов
        s13 = next(s for s in secs if s.section == "1.3")
        self.assertIn("не более 30%", s13.text)
        self.assertIn("более 30%", s13.text)

    def test_ib_project_table_preserves_empty_cells(self):
        _, sections = parse_sections(RAW / "ib-project.docx")
        section = next(section for section in sections if section.section == "1.2")
        rows = [line for line in section.lines if line.startswith("|")]
        self.assertEqual(rows[0], "| Обязательно | При наличии |")
        self.assertEqual(sum(row.startswith("|  | ") for row in rows), 3)

    def test_key_clauses_exist(self):
        for doc, sec, needle in [("ib-project", "2.3", "5 рабочих дней"), ("dbrct-project", "1.1.1", "анкет"),
                                 ("tech-project", "1.3", "Заявку на расчёт")]:
            c = mock.get_section(doc, sec)
            self.assertIsNotNone(c, f"{doc}::{sec}")
            self.assertIn(needle, c.text)


@unittest.skipUnless(HAVE_DATA, "нет DOCX в data/raw/")
class TestSearch(unittest.TestCase):
    def top(self, q, k=5):
        return [h.chunk.chunk_id for h in mock.search(q, k=k).hits]

    def test_synonym_project_manager(self):
        self.assertIn("ib-project::2.1", self.top("Кто назначает проджект менеджера на проекты по ИБ?"))

    def test_abbreviation(self):
        self.assertIn("dbrct-project::1.1.1", self.top("Что будет, если в ЗНП нет квал анкеты?"))

    def test_directorate_scope_no_leak(self):
        top = self.top("Дирекция ИБ: что будет, если SS не отвечает по карточке проекта?")
        self.assertTrue(all(not t.startswith("dbrct-project") for t in top[:3]), top)

    def test_contract_shape(self):
        res = mock.search("Кто отвечает за коммуникации с заказчиком?", k=3)
        d = res.to_dict()
        self.assertTrue({"hits", "restricted", "top_score"} <= set(d))
        self.assertIn("chunk_id", d["hits"][0]["chunk"])
        self.assertEqual(d["hits"][0]["chunk"]["source_type"], "regulation")


    def test_low_confidence_hint(self):
        self.assertTrue(mock.search("Какой дресс-код в офисе?").low_confidence)
        self.assertFalse(mock.search("Кто назначает РП на проект ИБ?").low_confidence)
        self.assertEqual(mock.search("Кто назначает РП на проект ИБ?").scope, ["ИБ"])


@unittest.skipUnless(HAVE_DATA, "нет DOCX в data/raw/")
class TestClarifyHint(unittest.TestCase):
    def test_abstract_question_is_ambiguous(self):
        res = mock.search("Что будет, если по заявке не хватает информации?")
        self.assertTrue(res.ambiguous_scope)
        self.assertGreaterEqual(len({o["directorate"] for o in res.scope_options}), 2)

    def test_explicit_directorate_is_not_ambiguous(self):
        self.assertFalse(mock.search("Кто назначает РП на проекты ИБ?").ambiguous_scope)

    def test_unique_term_is_not_ambiguous(self):  # квал. анкета есть только в ДБРЦТ
        self.assertFalse(mock.search("Что будет, если в ЗНП нет квал анкеты?").ambiguous_scope)

    def test_directorate_specific_role_is_not_ambiguous(self):  # TPS есть только в Техдирекции
        self.assertFalse(mock.search("TPS говорит, что в заявке не хватает данных для РТЗ — что со сроками?").ambiguous_scope)


@unittest.skipUnless(HAVE_DATA, "нет DOCX в data/raw/")
class TestContextAndSynonyms(unittest.TestCase):
    def test_nested_clause_gets_parent(self):
        hit = next(h for h in mock.search("Что будет, если в ЗНП нет квал анкеты?").hits
                   if h.chunk.chunk_id == "dbrct-project::1.1.1")
        self.assertEqual(hit.parents[0]["section"], "1.1")
        self.assertIn("квалификационной анкеты", hit.parents[0]["text"])

    def test_preposition_po_is_not_software(self):
        self.assertNotIn("Программное обеспечение", mock.search("Можно ли начать работы по проекту без договора?").expanded_query)
        self.assertIn("Программное обеспечение", mock.search("Кто готовит спецификацию на ПО?").expanded_query)


@unittest.skipUnless(HAVE_DATA, "нет DOCX в data/raw/")
class TestQaEntries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._qa = mock.QA_PATH
        mock.QA_PATH = Path(self.tmp.name) / "qa.jsonl"
        mock.reload()

    def tearDown(self):
        mock.QA_PATH = self._qa
        mock.reload()
        self.tmp.cleanup()

    def test_only_confirmed_is_searchable(self):
        e = {"qa_entry_id": "qa-1", "ticket_id": "Q-0001", "question": "Сколько дней юристы согласуют ТКП?",
             "answer": "Юридический отдел согласует ТКП за 2 рабочих дня (учебный ответ).",
             "doc_id": "tech-project", "section": "6.3", "status": "draft"}
        mock.upsert_qa_entry(e)
        self.assertFalse(any(h.chunk.source_type == "qa_entry" for h in mock.search("юристы согласуют ТКП").hits))
        mock.upsert_qa_entry({**e, "status": "confirmed"})
        hits = mock.search("За сколько дней юристы согласовывают ТКП?").hits
        self.assertEqual(hits[0].chunk.source_type, "qa_entry")
        self.assertEqual(json.loads(mock.QA_PATH.read_text(encoding="utf-8").splitlines()[0])["qa_entry_id"], "qa-1")


if __name__ == "__main__":
    unittest.main()
