import asyncio
import os
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import main, quality_mail


class QualityMailTests(unittest.TestCase):
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

    def test_publishes_only_the_matching_reply_once(self):
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
            self.assertEqual(str(outgoing["Cc"]), "employee@demo.invalid")

            def receive(sender, reference, answer, cc=None):
                message = EmailMessage()
                message["From"] = sender
                message["To"] = outgoing["From"]
                if cc:
                    message["Cc"] = cc
                message["In-Reply-To"] = reference
                message.set_content(answer)
                quality_mail.publish_reply(main.database, message.as_bytes())

            receive(outgoing["Cc"], outgoing["Message-ID"], "Без подтверждения")
            receive(outgoing["To"], "<another-ticket@demo.invalid>", "Другое обращение")
            self.assertEqual(main.published_answers(employee), [])
            receive(outgoing["To"], outgoing["Message-ID"], "Пять рабочих дней.", cc=outgoing["Cc"])
            receive(outgoing["To"], outgoing["Message-ID"], "Дубликат")
            [entry] = main.published_answers(employee)
            self.assertEqual(entry["answer"], "Пять рабочих дней.")
            self.assertEqual(entry["confirmed_by"], "quality-mail")
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
