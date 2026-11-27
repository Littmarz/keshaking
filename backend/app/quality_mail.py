import asyncio
import json
import logging
import os
import re
from base64 import b64encode
from contextlib import closing
from email import policy
from email.errors import MessageError
from email.message import EmailMessage
from email.parser import BytesParser
from smtplib import SMTP
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4


logger = logging.getLogger(__name__)
PUNKT_ADDRESS = "punkt@demo.invalid"
QUALITY_ADDRESS = "quality@demo.invalid"
TICKET_REFERENCE = re.compile(r"<Q-([0-9]{4,18})\.([0-9a-f]{64})@punkt\.demo\.invalid>")
MAILPIT_ID = re.compile(r"[a-zA-Z0-9_-]+")
MAILPIT_USER = "punkt"
PAGE_SIZE = 50
MAX_MAIL_BYTES = 128 * 1024
MAIL_RESERVATION_SECONDS = 60
MAIL_RETRY_SECONDS = 15


def employee_address(user_id: str) -> str:
    return f"{user_id}@demo.invalid"


def ticket_reference(ticket_seq: int, token: str) -> str:
    return f"<Q-{ticket_seq:04d}.{token}@punkt.demo.invalid>"


def copy_reference(ticket_id: str) -> str:
    return f"<{ticket_id}.notice@punkt.demo.invalid>"


def mailpit_password() -> str:
    password = os.environ.get("PUNKT_MAIL_PASSWORD", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", password):
        raise RuntimeError("Set PUNKT_MAIL_PASSWORD using letters, digits, hyphens or underscores.")
    return password


def open_mailpit(url: str):
    credentials = b64encode(f"{MAILPIT_USER}:{mailpit_password()}".encode()).decode()
    request = Request(url)
    request.add_unredirected_header("Authorization", f"Basic {credentials}")
    return urlopen(request, timeout=2)


def send(message: EmailMessage) -> None:
    host = os.environ.get("PUNKT_MAIL_SMTP_HOST", "mailpit")
    port = int(os.environ.get("PUNKT_MAIL_SMTP_PORT", "1025"))
    with closing(SMTP(host, port, timeout=2)) as smtp:
        smtp.login(MAILPIT_USER, mailpit_password())
        smtp.send_message(message)


def deliver_queued_mail(database, queue: str, select_next: str, build_message) -> None:
    # Reserve one row so other workers skip it while SMTP runs outside SQLite's write lock.
    with database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(select_next).fetchone()
        if row is None:
            return
        reserved_until = connection.execute(
            f"UPDATE {queue} SET retry_at = unixepoch() + ? WHERE ticket_seq = ? RETURNING retry_at",
            (MAIL_RESERVATION_SECONDS, row["seq"]),
        ).fetchone()["retry_at"]

    ticket_id = f"Q-{row['seq']:04d}"
    message = build_message(row, ticket_id)
    try:
        send(message)
    except OSError as exc:
        logger.warning("Mail in %s for %s delayed: %s", queue, ticket_id, type(exc).__name__)
        update, params = "retry_at = unixepoch() + ?", (MAIL_RETRY_SECONDS,)
    else:
        update, params = "sent_at = unixepoch()", ()

    with database() as connection:
        connection.execute(
            f"UPDATE {queue} SET {update} WHERE ticket_seq = ? AND retry_at = ? AND sent_at IS NULL",
            (*params, row["seq"], reserved_until),
        )


def ticket_message(row, ticket_id: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = PUNKT_ADDRESS
    message["To"] = QUALITY_ADDRESS
    message["Subject"] = f"Пункт: обращение {ticket_id}"
    message["Message-ID"] = ticket_reference(row["seq"], row["reply_token"])
    context = json.loads(row["context"])
    conflict = ""
    if sources := context.get("conflict_sources"):
        references = "\n".join(f"- {source['doc_title']} ({source['section']})" for source in sources)
        conflict = f"{context['reason']}\n{references}\n\n"
    message.set_content(
        f"Обращение {ticket_id}\n\nВопрос сотрудника\n{row['question']}\n\n"
        f"{conflict}"
        "Ответьте на письмо простым текстом, затем проверьте и подтвердите черновик в кабинете службы качества."
    )
    return message


def deliver_next(database) -> None:
    deliver_queued_mail(
        database, "quality_mail",
        """SELECT t.seq, t.question, t.context, m.reply_token
           FROM quality_mail AS m JOIN tickets AS t ON t.seq = m.ticket_seq
           WHERE m.sent_at IS NULL AND m.retry_at <= unixepoch() AND m.reply_token IS NOT NULL
           ORDER BY m.retry_at, t.seq LIMIT 1""",
        ticket_message,
    )


def ticket_copy_message(row, ticket_id: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = PUNKT_ADDRESS
    message["To"] = employee_address(row["user_id"])
    message["Subject"] = f"Пункт: уведомление об обращении {ticket_id}"
    message["Message-ID"] = copy_reference(ticket_id)
    message.set_content(
        f"Обращение {ticket_id} направлено в службу качества.\n\n"
        f"Вопрос сотрудника\n{row['question']}\n\n"
        "Подтверждённый ответ появится в чате и придёт в отдельном письме."
    )
    return message


def deliver_next_copy(database) -> None:
    deliver_queued_mail(
        database, "quality_mail_copy",
        """SELECT t.seq, t.question, t.user_id
           FROM quality_mail_copy AS m JOIN tickets AS t ON t.seq = m.ticket_seq
           JOIN quality_mail AS original ON original.ticket_seq = m.ticket_seq
           WHERE m.sent_at IS NULL AND m.retry_at <= unixepoch() AND original.sent_at IS NOT NULL
           ORDER BY m.retry_at, t.seq LIMIT 1""",
        ticket_copy_message,
    )


def queue_confirmed_reply(connection, ticket_seq: int) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO quality_reply_mail (ticket_seq) VALUES (?)", (ticket_seq,),
    )


def reply_message(row, ticket_id: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = PUNKT_ADDRESS
    message["To"] = employee_address(row["user_id"])
    message["Subject"] = f"Пункт: ответ службы качества по обращению {ticket_id}"
    message["Message-ID"] = f"<{ticket_id}.reply@punkt.demo.invalid>"
    message["In-Reply-To"] = copy_reference(ticket_id)
    message.set_content(row["answer"])
    return message


def deliver_next_reply(database) -> None:
    deliver_queued_mail(
        database, "quality_reply_mail",
        """SELECT t.seq, t.user_id, q.answer
           FROM quality_reply_mail AS m JOIN tickets AS t ON t.seq = m.ticket_seq
           JOIN qa_entries AS q ON q.ticket_seq = m.ticket_seq
           WHERE m.sent_at IS NULL AND m.retry_at <= unixepoch() AND q.status = 'confirmed'
           ORDER BY m.retry_at, m.ticket_seq LIMIT 1""",
        reply_message,
    )


def send_demo_reply(ticket_seq: int, reply_token: str, answer: str) -> None:
    """Simulates a plain-text answer from a quality specialist in the local test mailbox."""
    message = EmailMessage()
    message["From"] = QUALITY_ADDRESS
    message["To"] = PUNKT_ADDRESS
    message["Subject"] = f"Re: Пункт: обращение Q-{ticket_seq:04d}"
    message["In-Reply-To"] = ticket_reference(ticket_seq, reply_token)
    message.set_content(answer)
    send(message)


def draft_reply(database, raw: bytes) -> None:
    if len(raw) > MAX_MAIL_BYTES:
        return
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
        sender = message["From"]
        reference = TICKET_REFERENCE.fullmatch(str(message["In-Reply-To"] or ""))
        if sender is None or [a.addr_spec.casefold() for a in sender.addresses] != [QUALITY_ADDRESS] or not reference:
            return
        to = {a.addr_spec.casefold() for recipients in message.get_all("To", []) for a in recipients.addresses}
        if PUNKT_ADDRESS not in to:
            return
        body = message.get_body(preferencelist=("plain",))
        answer = body.get_content().strip() if body else ""
    except (MessageError, LookupError, TypeError, ValueError):
        return
    if not 1 <= len(answer) <= 8000:
        return

    ticket_seq, token = int(reference[1]), reference[2]
    with database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        inserted = connection.execute(
            """INSERT INTO qa_entries (qa_entry_id, ticket_seq, answer)
               SELECT ?, t.seq, ? FROM tickets AS t JOIN quality_mail AS m ON m.ticket_seq = t.seq
               WHERE t.seq = ? AND t.status = 'open' AND m.reply_token = ?
               ON CONFLICT (ticket_seq) DO NOTHING""",
            (uuid4().hex, answer, ticket_seq, token),
        )
        if inserted.rowcount == 1:
            connection.execute("UPDATE tickets SET status = 'answered' WHERE seq = ?", (ticket_seq,))


def receive_replies(database, seen: set[str]) -> None:
    base = os.environ.get("PUNKT_MAIL_HTTP_URL", "http://mailpit:8025").rstrip("/")
    start = 0
    while True:
        query = urlencode({
            "query": f"from:{QUALITY_ADDRESS} to:{PUNKT_ADDRESS}",
            "start": start,
            "limit": PAGE_SIZE,
        })
        with open_mailpit(f"{base}/api/v1/search?{query}") as response:
            messages = json.load(response)["messages"] or []
        for row in reversed(messages):
            message_id = row["ID"]
            if message_id in seen or not MAILPIT_ID.fullmatch(message_id):
                continue
            try:
                with open_mailpit(f"{base}/api/v1/message/{message_id}/raw") as response:
                    draft_reply(database, response.read(MAX_MAIL_BYTES + 1))
            except HTTPError as exc:
                if exc.code != 404:
                    raise
            seen.add(message_id)
        if len(messages) < PAGE_SIZE:
            return
        start += PAGE_SIZE


async def deliver_mail(database) -> None:
    """Фоновая почта не должна умирать от одной ошибки: «database is locked» из SQLite раньше
    завершала задачу навсегда, и сценарий 3 молча переставал работать до перезапуска."""
    seen: set[str] = set()
    mailbox_unavailable = False

    async def attempt(step, *args, what: str) -> None:
        try:
            await asyncio.to_thread(step, *args)
        except Exception:
            logger.exception("%s не удалось, повторю", what)

    while True:
        await attempt(deliver_next, database, what="Отправка письма службе качества")
        await attempt(deliver_next_copy, database, what="Уведомление сотрудника")
        try:
            await asyncio.to_thread(receive_replies, database, seen)
            mailbox_unavailable = False
        except (OSError, ValueError, KeyError) as exc:
            if not mailbox_unavailable:
                logger.warning("Test mailbox unavailable: %s", type(exc).__name__)
            mailbox_unavailable = True
        except Exception:
            logger.exception("Разбор ответов службы качества не удался, повторю")
        await attempt(deliver_next_reply, database, what="Отправка ответа сотруднику")
        await asyncio.sleep(2)
