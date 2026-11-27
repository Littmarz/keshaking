import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import main
from app.agent import document_search_ready
from retrieval import mock


class ReadinessTests(unittest.TestCase):
    def test_readiness_requires_only_dependencies_configured_for_the_mode(self):
        cases = [
            ("mock", "demo", False, False, 200),
            ("real", "demo", True, False, 200),
            ("real", "demo", False, False, 503),
            ("real", "documents", True, False, 503),
            ("real", "documents", False, True, 503),
            ("real", "documents", True, True, 200),
        ]
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ, {
                "KESHAKING_DB_PATH": str(Path(folder) / "punkt.sqlite3"),
                "PUNKT_TEST_MAIL": "0",
                "PUNKT_EMPLOYEE_PASSWORD": "staff-test-secret",
                "PUNKT_QUALITY_PASSWORD": "quality-test-secret",
            }
        ):
            async def initialize():
                async with main.lifespan(main.app):
                    pass

            asyncio.run(initialize())
            for mode, sources, model_ok, search_ok, expected in cases:
                with self.subTest(mode=mode, sources=sources, model=model_ok, search=search_ok), patch.dict(
                    os.environ, {"AGENT_MODE": mode, "AGENT_SOURCES": sources}
                ), patch("app.main.model_ready", return_value=model_ok), patch(
                    "app.main.document_search_ready", return_value=search_ok
                ):
                    self.assertEqual(main.readiness().status_code, expected)

    def test_readiness_fails_for_uninitialized_database(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ, {"AGENT_MODE": "mock", "KESHAKING_DB_PATH": str(Path(folder) / "empty.sqlite3")}
        ):
            response = main.readiness()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.body), {"db": "down"})

    def test_regulation_index_must_contain_readable_accessible_sections(self):
        chunk = mock.Chunk("doc::1", "doc", "Регламент", "1", "Заявку проверяет координатор.")
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            index = directory / "chunks.jsonl"
            contents = [
                ("", False),
                ("broken JSON", False),
                (json.dumps(dict(chunk.to_dict(), access_level="quality")), False),
                (json.dumps(dict(chunk.to_dict(), source_type="qa_entry")), False),
                (json.dumps(chunk.to_dict()), True),
            ]
            for corpus, expected in contents:
                with self.subTest(corpus=corpus), patch.object(mock, "PROCESSED_DIR", directory), patch.object(
                    mock, "QA_PATH", directory / "qa_entries.jsonl"
                ), patch.object(mock, "_INDEX", None):
                    index.write_text(corpus, encoding="utf-8")
                    self.assertEqual(document_search_ready(), expected)
