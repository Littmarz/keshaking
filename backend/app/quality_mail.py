import asyncio
import logging
from contextlib import closing
from email.message import EmailMessage
from smtplib import SMTP


logger = logging.getLogger(__name__)


def deliver_next(database):
    with database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """SELECT t.seq, t.question, t.session_id
               FROM quality_mail AS m JOIN tickets AS t ON t.seq = m.ticket_seq
               WHERE m.sent_at IS NULL AND m.retry_at <= unixepoch()
               ORDER BY m.retry_at, t.seq LIMIT 1""",
        ).fetchone()
        if row is None:
            return

        ticket_id = f"Q-{row['seq']:04d}"
        message = EmailMessage()
        message["From"] = "punkt@demo.invalid"
        message["To"] = "quality@demo.invalid"
        message["Subject"] = f"Пункт: обращение {ticket_id}"
        message["Message-ID"] = f"<{ticket_id}.{row['session_id']}@punkt.demo.invalid>"
        message.set_content(f"Обращение {ticket_id}\n\nВопрос сотрудника\n{row['question']}\n\n"
                            "Откройте это обращение в разделе «Служба качества» приложения «Пункт».")
        try:
            with closing(SMTP("mailpit", 1025, timeout=2)) as smtp:
                smtp.send_message(message)
        except OSError as exc:
            logger.warning("Test mail for %s delayed: %s", ticket_id, type(exc).__name__)
            connection.execute(
                "UPDATE quality_mail SET retry_at = unixepoch() + 15 WHERE ticket_seq = ?", (row["seq"],),
            )
            return
        connection.execute("UPDATE quality_mail SET sent_at = unixepoch() WHERE ticket_seq = ?", (row["seq"],))


async def deliver_mail(database):
    while True:
        await asyncio.to_thread(deliver_next, database)
        await asyncio.sleep(2)
