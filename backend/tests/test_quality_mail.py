import asyncio
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from pathlib import Path
from threading import Event
from unittest.mock import patch

from fastapi import HTTPException

from app import main, quality_mail


class QualityMailTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {
            "PUNKT_EMPLOYEE_PASSWORD": "staff-test-secret",
            "PUNKT_QUALITY_PASSWORD": "quality-test-secret",
        }))

    def test_mailbox_send_does_not_block_chat_writes_or_send_twice(self):
        cases = (("notice", quality_mail.deliver_next), ("reply", quality_mail.deliver_next_reply))
        for kind, deliver in cases:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder, patch.dict(
                os.environ, {"KESHAKING_DB_PATH": str(Path(folder) / "punkt.sqlite3"), "PUNKT_TEST_MAIL": "0"}
            ):
                async def initialize():
                    async with main.lifespan(main.app):
                        pass

                asyncio.run(initialize())
                employee = main.USERS["employee"]
                session = main.create_session(employee)["session_id"]
                with patch.dict(os.environ, {"PUNKT_TEST_MAIL": "1"}):
                    with main.database() as connection:
                        ticket = main.create_ticket(connection, employee["user_id"], session, "Какой срок?", "Нет ответа.")
                    if kind == "reply":
                        answer = main.save_ticket_answer(ticket["ticket_id"], main.TicketAnswer(answer="Пять дней."))
                        main.confirm_answer(answer["qa_entry_id"], main.USERS["quality"])

                smtp_started = Event()
                smtp_release = Event()

                def slow_send(_message):
                    smtp_started.set()
                    if not smtp_release.wait(10):
                        raise AssertionError("Test did not release mocked SMTP")

                with patch.object(quality_mail, "send", side_effect=slow_send) as send, ThreadPoolExecutor(2) as pool:
                    first = pool.submit(deliver, main.database)
                    try:
                        self.assertTrue(smtp_started.wait(5), "Delivery did not reach SMTP")
                        with main.database() as connection:
                            connection.execute("PRAGMA busy_timeout = 0")
                            connection.execute(
                                "INSERT INTO sessions (session_id, user_id, created_at) VALUES (?, ?, datetime('now'))",
                                ("chat-created-during-smtp", employee["user_id"]),
                            )
                            connection.execute(
                                "INSERT INTO messages (session_id, message_id, role, content) VALUES (?, ?, 'user', ?)",
                                ("chat-created-during-smtp", f"smtp-{kind}", "Вопрос во время отправки письма"),
                            )
                        self.assertIn("chat-created-during-smtp", {item["session_id"] for item in main.sessions(employee)})
                        second = pool.submit(deliver, main.database)
                        second.result(timeout=5)
                    finally:
                        smtp_release.set()
                    first.result(timeout=5)
                    deliver(main.database)
                    send.assert_called_once()

    def test_mixed_conflict_creates_separate_ticket_and_notifies_quality_once(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ, {"KESHAKING_DB_PATH": str(Path(folder) / "punkt.sqlite3"),
                         "PUNKT_TEST_MAIL": "0", "AGENT_MODE": "real"}
        ):
            async def initialize():
                async with main.lifespan(main.app):
                    pass

            asyncio.run(initialize())
            employee = main.USERS["employee"]
            session = main.create_session(employee)["session_id"]
            question = "Кто проверяет заявку?"
            with main.database() as connection:
                earlier = main.create_ticket(connection, employee["user_id"], session, question, "Нет ответа.")
            citations = [
                {"chunk_id": "doc::1.2", "doc_title": "Регламент", "section": "1.2", "source_type": "regulation"},
                {"chunk_id": "qa::1", "doc_title": "Разъяснение", "section": "Q-0001", "source_type": "qa_entry"},
            ]
            conflict = {"status": "conflict", "answer": "Применяйте регламент.", "citations": citations,
                        "reason": "Разъяснение расходится с регламентом."}
            payload = main.ChatRequest(session_id=session, question=question)
            with patch.dict(os.environ, {"PUNKT_TEST_MAIL": "1"}), patch(
                "app.main.run_agent", side_effect=lambda *_: conflict.copy()
            ):
                first = main.ask_and_save(payload, employee)
                repeated = main.ask_and_save(payload, employee)
            ticket = first["ticket"]["ticket_id"]
            self.assertNotEqual(ticket, earlier["ticket_id"])
            self.assertEqual(repeated["ticket"]["ticket_id"], ticket)
            self.assertFalse(repeated["ticket"]["created"])
            self.assertEqual(main.ticket_detail(ticket)["context"]["conflict_sources"], citations)
            with patch.object(quality_mail, "send") as send:
                quality_mail.deliver_next(main.database)
                quality_mail.deliver_next(main.database)
            send.assert_called_once()
            message = send.call_args.args[0]
            self.assertIn("расходится с регламентом", message.get_content())
            self.assertIn("Регламент (1.2)", message.get_content())

    def test_mail_creates_one_private_draft_until_expert_confirms(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ, {"KESHAKING_DB_PATH": str(Path(folder) / "punkt.sqlite3"), "PUNKT_TEST_MAIL": "0"}
        ):
            async def initialize():
                async with main.lifespan(main.app):
                    pass

            asyncio.run(initialize())
            employee = main.USERS["employee"]
            session = main.create_session(employee)["session_id"]
            with patch.dict(os.environ, {"PUNKT_TEST_MAIL": "1"}), main.database() as connection:
                main.create_ticket(connection, employee["user_id"], session, "Какой срок?", "Нет ответа.")
            with patch.object(quality_mail, "send") as send:
                quality_mail.deliver_next(main.database)
                quality_mail.deliver_next(main.database)
            send.assert_called_once()
            outgoing = send.call_args.args[0]
            self.assertIsNone(outgoing["Cc"])
            self.assertEqual(str(outgoing["To"]), quality_mail.QUALITY_ADDRESS)
            with patch.object(quality_mail, "send") as send:
                quality_mail.deliver_next_copy(main.database)
                quality_mail.deliver_next_copy(main.database)
            send.assert_called_once()
            copy = send.call_args.args[0]
            self.assertEqual(str(copy["To"]), "employee@demo.invalid")
            self.assertNotIn(str(outgoing["Message-ID"]), copy.as_string())
            self.assertEqual(str(copy["Message-ID"]), quality_mail.copy_reference("Q-0001"))

            def receive(sender, reference, answer):
                message = EmailMessage()
                message["From"] = sender
                message["To"] = outgoing["From"]
                message["In-Reply-To"] = reference
                message.set_content(answer)
                quality_mail.draft_reply(main.database, message.as_bytes())

            receive(copy["To"], outgoing["Message-ID"], "Ответ сотрудника")
            receive(outgoing["To"], copy["Message-ID"], "Ответ на уведомление")
            self.assertIsNone(main.ticket_detail("Q-0001")["qa_entry"])
            receive(outgoing["To"], outgoing["Message-ID"], "Пять рабочих дней.")
            receive(outgoing["To"], outgoing["Message-ID"], "Повторное письмо")
            ticket = main.ticket_detail("Q-0001")
            self.assertEqual(ticket["status"], "answered")
            draft = ticket["qa_entry"]
            self.assertEqual((draft["status"], draft["answer"]), ("draft", "Пять рабочих дней."))
            self.assertIsNone(draft["confirmed_by"])
            self.assertIsNone(main.my_tickets(employee)[0]["answer"])
            self.assertEqual(main.published_answers(employee), [])
            self.assertEqual(main.session_messages(session, employee)["messages"], [])
            with patch.object(quality_mail, "send") as send:
                quality_mail.deliver_next_reply(main.database)
            send.assert_not_called()

            with patch.dict(os.environ, {"PUNKT_TEST_MAIL": "1"}):
                entry = main.confirm_answer(draft["qa_entry_id"], main.USERS["quality"])
            self.assertEqual((entry["status"], entry["confirmed_by"]), ("confirmed", "quality"))
            self.assertEqual(main.ticket_detail(entry["ticket_id"])["status"], "published")
            [reply] = main.session_messages(session, employee)["messages"]
            self.assertEqual((reply["role"], reply["content"], reply["ticket_id"]),
                             ("quality", entry["answer"], entry["ticket_id"]))
            with patch.object(quality_mail, "send") as send:
                quality_mail.deliver_next_reply(main.database)
                quality_mail.deliver_next_reply(main.database)
            send.assert_called_once()
            message = send.call_args.args[0]
            self.assertEqual(str(message["To"]), "employee@demo.invalid")
            self.assertEqual(message.get_content().strip(), entry["answer"])

    def test_upgrade_returns_legacy_mail_confirmation_to_review(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ, {"KESHAKING_DB_PATH": str(Path(folder) / "punkt.sqlite3"), "PUNKT_TEST_MAIL": "0"}
        ):
            async def initialize():
                async with main.lifespan(main.app):
                    pass

            asyncio.run(initialize())
            employee = main.USERS["employee"]
            session = main.create_session(employee)["session_id"]
            with patch.dict(os.environ, {"PUNKT_TEST_MAIL": "1"}), main.database() as db:
                legacy = main.create_ticket(db, employee["user_id"], session, "Какой срок?", "Нет ответа.")
                verified = main.create_ticket(db, employee["user_id"], session, "Кто отвечает?", "Нет ответа.")
            legacy_entry = main.save_ticket_answer(legacy["ticket_id"], main.TicketAnswer(answer="Требует проверки."))
            verified_entry = main.save_ticket_answer(verified["ticket_id"], main.TicketAnswer(answer="Проверенный ответ."))
            with main.database() as db:
                db.execute("DROP TABLE quality_mail_copy")
                db.execute("ALTER TABLE quality_mail DROP COLUMN reply_token")
                db.execute("UPDATE quality_mail SET sent_at = unixepoch()")
                db.execute("UPDATE tickets SET status = 'published'")
                db.execute("UPDATE qa_entries SET status = 'confirmed', confirmed_by = 'quality'")
                db.execute("UPDATE qa_entries SET confirmed_by = 'quality-mail' WHERE qa_entry_id = ?",
                           (legacy_entry["qa_entry_id"],))
                db.execute("INSERT INTO quality_reply_mail (ticket_seq) SELECT seq FROM tickets")

            asyncio.run(initialize())
            asyncio.run(initialize())
            self.assertEqual(main.ticket_detail(legacy["ticket_id"])["status"], "answered")
            self.assertEqual(main.ticket_detail(legacy["ticket_id"])["qa_entry"]["answer"], "Требует проверки.")
            self.assertEqual([entry["qa_entry_id"] for entry in main.published_answers(employee)],
                             [verified_entry["qa_entry_id"]])
            with main.database() as db:
                [legacy_mail, verified_mail] = db.execute(
                    "SELECT sent_at, reply_token FROM quality_mail ORDER BY ticket_seq"
                ).fetchall()
                self.assertIsNone(legacy_mail["sent_at"])
                self.assertEqual(len(legacy_mail["reply_token"]), 64)
                self.assertIsNotNone(verified_mail["sent_at"])
                self.assertEqual(db.execute("SELECT count(*) FROM quality_reply_mail").fetchone()[0], 1)

    def test_web_answer_is_visible_after_confirmation_and_email_retries(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ, {"KESHAKING_DB_PATH": str(Path(folder) / "punkt.sqlite3"),
                         "PUNKT_TEST_MAIL": "0", "AGENT_MODE": "real"}
        ):
            async def initialize():
                async with main.lifespan(main.app):
                    pass

            asyncio.run(initialize())
            employee = main.USERS["employee"]
            specialist = main.USERS["quality"]
            session = main.create_session(employee)["session_id"]
            unresolved = {"status": "escalated", "answer": "Ответа в базе пока нет.", "reason": "Нет данных."}
            with patch.dict(os.environ, {"PUNKT_TEST_MAIL": "1"}), patch.object(
                main, "run_agent", return_value=unresolved
            ):
                notice = main.ask_and_save(main.ChatRequest(session_id=session, question="Какой срок?"), employee)
                ticket_id = notice["ticket"]["ticket_id"]
                answer = "Ответ службы качества."
                draft = main.save_ticket_answer(ticket_id, main.TicketAnswer(answer=answer))
                self.assertEqual([message["role"] for message in main.session_messages(session, employee)["messages"]],
                                 ["user", "assistant"])
                main.confirm_answer(draft["qa_entry_id"], specialist)
                main.confirm_answer(draft["qa_entry_id"], specialist)

            history = main.session_messages(session, employee)["messages"]
            self.assertEqual([message["role"] for message in history], ["user", "assistant", "quality"])
            self.assertEqual(history[-1]["ticket_id"], ticket_id)
            self.assertEqual(history[-1]["content"], answer)
            with self.assertRaises(HTTPException) as exc:
                main.session_messages(session, specialist)
            self.assertEqual(exc.exception.status_code, 404)

            with patch.object(quality_mail, "send", side_effect=OSError) as send:
                quality_mail.deliver_next_reply(main.database)
                quality_mail.deliver_next_reply(main.database)
            send.assert_called_once()
            failed_message_id = str(send.call_args.args[0]["Message-ID"])
            with main.database() as connection:
                connection.execute("UPDATE quality_reply_mail SET retry_at = 0")
            with patch.object(quality_mail, "send") as send:
                quality_mail.deliver_next_reply(main.database)
                quality_mail.deliver_next_reply(main.database)
            send.assert_called_once()
            message = send.call_args.args[0]
            self.assertEqual(str(message["Message-ID"]), failed_message_id)
            self.assertEqual(str(message["To"]), "employee@demo.invalid")
            self.assertEqual(message.get_content().strip(), answer)


if __name__ == "__main__":
    unittest.main()
