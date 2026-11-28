"""Проверка eval --target api на заглушке HTTP API «Пункта» (только стандартная библиотека)."""

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from eval import run


PASSWORDS = {"employee": "staff-test-secret", "quality": "quality-test-secret"}


class _Stub(BaseHTTPRequestHandler):
    tickets: dict = {}
    confirmed: list = []

    def log_message(self, *a):  # тишина в выводе тестов
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send({"db": "ok", "search": "ok", "llm": "ok"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        p = self.path
        if p == "/auth/login":
            if body.get("password") != PASSWORDS.get(body["user_id"]):
                return self._send({"detail": "invalid password"}, 401)
            return self._send({"token": body["user_id"], "user": {}})
        if p == "/chat/sessions":
            return self._send({"session_id": "s"})
        if p == "/chat/ask":
            q = body["question"]
            steps = [{"detail": {"prompt_tokens": 1000, "completion_tokens": 100}},
                     {"detail": {"prompt_tokens": 2000, "completion_tokens": 50}}]
            if "юрист" in q.lower() and self.confirmed:
                return self._send({"status": "answered", "answer": "Учебный ответ: 2 рабочих дня.", "steps": steps,
                                   "citations": [{"doc_id": "qa", "section": "Q-0001", "source_type": "qa_entry"}]})
            if "юрист" in q.lower():
                self.tickets["Q-0001"] = q
                return self._send({"status": "escalated", "answer": "Отправил вопрос в службу качества.", "steps": steps,
                                   "ticket": {"ticket_id": "Q-0001", "created": True}})
            return self._send({"status": "answered", "answer": "Координатор департамента назначает РП.", "steps": steps,
                               "citations": [{"doc_id": "ib-project", "section": "2.1", "source_type": "regulation"}]})
        if p.startswith("/tickets/") and p.endswith("/answer"):
            return self._send({"qa_entry_id": "e1"})
        if p == "/qa/e1/confirm":
            self.confirmed.append("e1")
            return self._send({"status": "confirmed"})
        self._send({"detail": "not found"}, 404)


class TestApiRunner(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {
            f"PUNKT_{role.upper()}_PASSWORD": password for role, password in PASSWORDS.items()
        }))
        _Stub.tickets, _Stub.confirmed = {}, []
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self._reports = run.REPORTS

    def tearDown(self):
        self.srv.shutdown()
        run.REPORTS = self._reports

    def test_quality_loop_and_tokens(self):
        import tempfile
        from pathlib import Path
        run.REPORTS = Path(tempfile.mkdtemp())
        cases = [
            {"id": "a", "category": "synonym", "turns": ["Кто назначает проджект менеджера на проекты ИБ?"],
             "expected_status": ["answered"], "expected_sources": [{"doc_id": "ib-project", "section": "2.1"}],
             "must_contain": ["Координатор"], "must_not_cite": []},
            {"id": "b", "category": "escalate", "turns": ["Сколько дней на согласование ТКП с юристами?"],
             "expected_status": ["escalated"], "expected_sources": [], "must_contain": ["качеств"], "must_not_cite": [],
             "qa_confirm": {"answer": "2 рабочих дня", "doc_id": "tech-project", "section": "6.3"}},
            {"id": "c", "category": "escalate", "turns": ["За сколько дней юристы согласуют ТКП?"],
             "expected_status": ["answered"], "expected_sources": [{"source_type": "qa_entry"}], "must_contain": [],
             "must_not_cite": []},
        ]
        rows, summary = run.run_api(cases, 5, self.base, timeout=5)
        self.assertEqual([r["status_ok"] for r in rows], [True, True, True])
        self.assertTrue(all(r["sources_ok"] for r in rows))
        self.assertEqual(rows[0]["prompt_tokens"], 3000)
        self.assertEqual(rows[0]["llm_calls"], 2)
        self.assertEqual(summary["avg_prompt_tokens"], 3000)
        self.assertTrue(list(run.REPORTS.glob("*_api_runs.jsonl")))


class TestAnswerEvaluation(unittest.TestCase):
    def test_equivalent_deadline_wording_preserves_the_strict_boundary(self):
        case = run.load(run.DATASET, {"c01"})[0]
        for phrase, correct in (("более 5 рабочих дней", True), ("более пяти рабочих дней", True),
                                ("через 5 рабочих дней", False), ("более 15 рабочих дней", False)):
            with self.subTest(phrase=phrase):
                answer = f"Если прошло {phrase}, статус «Отклонён». Для возврата нужен статус «Новый»."
                last = {"status": "answered", "answer": answer}
                self.assertEqual(run._judge(case, case["expected_status"], last, 0)["facts_ok"], correct)

    def test_missing_information_requires_correct_handoff_and_applicable_sources(self):
        case = run.load(run.DATASET, {"c17"})[0]
        source = {"doc_id": "tech-project", "section": "2.6"}
        wrong_source = {"doc_id": "dbrct-project", "section": "2.1.1"}
        correct = "TPS возвращает заявку SS на доработку. Сроки сдвигаются до получения данных."
        wrong_roles = "Архитектор возвращает заявку TPS на доработку. Сроки сдвигаются до получения данных."
        for answer, citations, facts_ok, leaks in (
            (correct, [source], True, []),
            (wrong_roles, [source], False, []),
            (correct, [source, wrong_source], True, ["dbrct-project::2.1.1"]),
        ):
            with self.subTest(answer=answer, citations=citations):
                last = {"status": "answered", "answer": answer, "citations": citations}
                judged = run._judge(case, ["answered"], last, 0)
                self.assertEqual(judged["facts_ok"], facts_ok)
                self.assertEqual(judged["leak"], leaks)

    def test_conflict_requires_distinct_sources_with_expected_section(self):
        case = run.load(run.DATASET, {"c09"})[0]
        first = {"doc_id": "ib-project", "section": "2.3"}
        second = {"doc_id": "ib-project", "section": "2.3.1"}
        for citations, correct in (([first], False), ([first, first], False), ([first, second], True)):
            with self.subTest(citations=citations):
                last = {"status": "conflict", "citations": citations}
                self.assertEqual(run._judge(case, ["conflict"], last, 0)["sources_ok"], correct)


if __name__ == "__main__":
    unittest.main()
