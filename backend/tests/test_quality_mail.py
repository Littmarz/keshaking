import asyncio
import os
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from app import main, quality_mail


class QualityMailTests(unittest.TestCase):
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

            def receive(sender, reference, answer):
                message = EmailMessage()
                message["From"] = sender
                message["To"] = outgoing["From"]
                message["In-Reply-To"] = reference
                message.set_content(answer)
                quality_mail.publish_reply(main.database, message.as_bytes())

            receive(outgoing["Cc"], outgoing["Message-ID"], "Без подтверждения")
            receive(outgoing["To"], "<another-ticket@demo.invalid>", "Другое обращение")
            self.assertEqual(main.published_answers(employee), [])
            receive(outgoing["To"], outgoing["Message-ID"], "Пять рабочих дней.")
            receive(outgoing["To"], outgoing["Message-ID"], "Дубликат")
            [entry] = main.published_answers(employee)
            self.assertEqual(entry["answer"], "Пять рабочих дней.")
            self.assertEqual(entry["confirmed_by"], "quality-mail")
            self.assertEqual(main.ticket_detail(entry["ticket_id"])["status"], "published")


if __name__ == "__main__":
    unittest.main()
