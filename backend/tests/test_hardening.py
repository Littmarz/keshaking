"""Защита стенда: лимиты, размер запроса, заголовки, история по браузерам, очередь к модели, поток, почта."""
import asyncio
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import main, quality_mail


def run_asgi(app, method="POST", body=b"", headers=()):
    sent, chunks = [], [body]

    async def receive():
        return {"type": "http.request", "body": chunks.pop(0) if chunks else b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": method, "path": "/", "headers": list(headers), "query_string": b""}
    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    return start["status"], dict(start["headers"]), b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


async def echo(scope, receive, send):
    message = await receive()
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": message["body"]})


class Middleware(unittest.TestCase):
    def test_body_limit_rejects_declared_and_streamed_oversize(self):
        app = main.BodyLimit(echo, max_bytes=10)
        self.assertEqual(run_asgi(app, body=b"x" * 5)[2], b"x" * 5)
        self.assertEqual(run_asgi(app, body=b"x" * 11)[0], 413)
        self.assertEqual(run_asgi(app, body=b"", headers=[(b"content-length", b"999999")])[0], 413)

    def test_security_headers_are_added_once(self):
        status, headers, _ = run_asgi(main.SecurityHeaders(main.SecurityHeaders(echo)), body=b"ok")
        self.assertEqual(status, 200)
        self.assertIn(b"frame-ancestors 'none'", headers[b"content-security-policy"])
        self.assertEqual(headers[b"x-frame-options"], b"DENY")

    def test_api_schema_is_hidden_by_default(self):
        self.assertIsNone(main.app.openapi_url)
        self.assertIsNone(main.app.docs_url)


class RateLimit(unittest.TestCase):
    def test_sliding_window(self):
        limiter = main.RateLimiter()
        for _ in range(3):
            limiter.check("login:1.2.3.4", 3, 60)
        with self.assertRaises(HTTPException) as blocked:
            limiter.check("login:1.2.3.4", 3, 60)
        self.assertEqual(blocked.exception.status_code, 429)
        self.assertIn("Retry-After", blocked.exception.headers)
        limiter.check("login:5.6.7.8", 3, 60)  # другой адрес не задет


class Stand(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {
            "KESHAKING_DB_PATH": str(Path(folder) / "punkt.sqlite3"), "PUNKT_TEST_MAIL": "0", "AGENT_MODE": "mock",
            "AGENT_SOURCES": "demo", "PUNKT_EMPLOYEE_PASSWORD": "staff-test-secret",
            "PUNKT_QUALITY_PASSWORD": "quality-test-secret",
        }))
        self.enterContext(patch.object(main, "tokens", {}))
        self.enterContext(patch.object(main, "llm_queue", main.LlmQueue()))

        async def initialize():
            async with main.lifespan(main.app):
                pass
        asyncio.run(initialize())

    def user(self, visitor=None):
        token = main.login(main.LoginRequest(user_id="employee", password="staff-test-secret", visitor_id=visitor))["token"]
        return main.current_user(main.HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))

    def test_each_browser_has_its_own_history(self):
        first, second = self.user("a" * 32), self.user("b" * 32)
        self.assertEqual(first["role"], "employee")
        self.assertNotEqual(first["user_id"], second["user_id"])
        session = main.create_session(first)["session_id"]
        main.ask(main.ChatRequest(session_id=session, question="Кто проверяет тестовую заявку?"), first)
        self.assertEqual(len(main.sessions(first)), 1)
        self.assertEqual(main.sessions(second), [])
        with self.assertRaises(HTTPException):
            main.session_messages(session, second)
        self.assertEqual(self.user()["user_id"], "employee")  # без visitor_id — как раньше

    def test_visitor_id_is_validated(self):
        with self.assertRaises(ValueError):
            main.LoginRequest(user_id="employee", visitor_id="../../etc")

    def test_examples_follow_sources(self):
        self.assertEqual(main.examples(self.user())[0]["question"], "Кто проверяет тестовую заявку?")
        with patch.dict(os.environ, {"AGENT_SOURCES": "documents"}):
            self.assertIn("РП в дирекции ИБ", main.examples(self.user())[0]["question"])

    def test_second_question_in_same_chat_is_rejected_at_once(self):
        user = self.user("c" * 32)
        session = main.create_session(user)["session_id"]
        entered, release = threading.Event(), threading.Event()

        def slow(*_args):
            entered.set()
            release.wait(5)
            return {"status": "answered"}

        with patch.object(main, "answer_and_save", slow):
            worker = threading.Thread(target=main.ask_and_save, args=(main.ChatRequest(session_id=session, question="один"), user))
            worker.start()
            self.assertTrue(entered.wait(5))
            with self.assertRaises(HTTPException) as busy:
                main.ask_and_save(main.ChatRequest(session_id=session, question="два"), user)
            self.assertEqual(busy.exception.status_code, 409)
            release.set()
            worker.join(5)

    def test_queue_reports_position(self):
        queue, steps = main.LlmQueue(), []
        entered, release = threading.Event(), threading.Event()

        def hold():
            with queue.turn("s1"):
                entered.set()
                release.wait(5)

        holder = threading.Thread(target=hold)
        holder.start()
        self.assertTrue(entered.wait(5))
        waiter = threading.Thread(target=lambda: queue.turn("s2", steps.append).__enter__())
        waiter.start()
        release.set()
        holder.join(5)
        waiter.join(5)
        self.assertIn("впереди: 1", steps[0]["title"])

    def test_stream_starts_at_once_and_reports_errors(self):
        user = self.user("d" * 32)
        session = main.create_session(user)["session_id"]

        async def collect(question):
            response = await main.ask_stream(main.ChatRequest(session_id=session, question=question), user)
            return [chunk async for chunk in response.body_iterator]

        events = asyncio.run(collect("Кто проверяет тестовую заявку?"))
        self.assertTrue(events[0].startswith("event: step"))
        self.assertTrue(events[-1].startswith("event: result"))
        with patch.object(main, "run_mock", side_effect=main.AgentError("Модель недоступна")):
            events = asyncio.run(collect("Ещё вопрос"))
        self.assertTrue(events[-1].startswith("event: error"))
        self.assertEqual(json.loads(events[-1].split("data: ", 1)[1])["status"], 502)


class Mail(unittest.TestCase):
    def test_worker_survives_database_errors(self):
        calls = {"n": 0}

        def locked(_db):
            calls["n"] += 1
            raise sqlite3.OperationalError("database is locked")

        async def scenario():
            real_sleep = asyncio.sleep
            with patch.object(quality_mail, "deliver_next", locked), patch.object(quality_mail, "receive_replies", lambda *_: None), \
                    patch.object(quality_mail, "deliver_next_reply", lambda *_: None), \
                    patch.object(quality_mail.asyncio, "sleep", lambda _s: real_sleep(0)):
                task = asyncio.create_task(quality_mail.deliver_mail(lambda: None))
                await real_sleep(0.2)
                alive = not task.done()
                task.cancel()
                return alive

        with self.assertLogs(quality_mail.logger, level="ERROR"):
            self.assertTrue(asyncio.run(scenario()))
        self.assertGreater(calls["n"], 1)


if __name__ == "__main__":
    unittest.main()
