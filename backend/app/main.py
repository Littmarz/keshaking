import asyncio
import json
import os
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import asynccontextmanager, closing, contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, StringConstraints

from app.agent import (
    AgentError,
    conversation_question,
    document_corpus_available,
    document_search_enabled,
    model_ready,
    run_agent,
)
from app.mocks.fake_agent import run_mock
from app.mocks.knowledge import normalized_question
from app.quality_mail import deliver_mail, employee_address, receive_replies, send_demo_reply


USERS = {
    "employee": {"user_id": "employee", "name": "Сотрудник", "role": "employee"},
    "quality": {"user_id": "quality", "name": "Служба качества", "role": "quality"},
}
DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "processed" / "mock.sqlite3"
bearer = HTTPBearer(auto_error=False)
tokens: dict[str, str] = {}


@contextmanager
def database():
    database_path = Path(os.environ.get("KESHAKING_DB_PATH", str(DEFAULT_DB)))
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database_path, timeout=10)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        yield connection


@asynccontextmanager
async def lifespan(_app):
    if os.environ.get("AGENT_MODE", "mock") not in ("mock", "real"):
        raise RuntimeError("AGENT_MODE must be mock or real.")
    with database() as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                seq INTEGER PRIMARY KEY, message_id TEXT NOT NULL UNIQUE,
                session_id TEXT NOT NULL REFERENCES sessions(session_id),
                role TEXT NOT NULL, content TEXT NOT NULL, status TEXT, result TEXT
            );
            CREATE INDEX IF NOT EXISTS messages_by_session ON messages(session_id, seq);
            CREATE TABLE IF NOT EXISTS tickets (
                seq INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(session_id),
                user_id TEXT NOT NULL, created_at TEXT NOT NULL, question TEXT NOT NULL,
                context TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
                dedup_key TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS qa_entries (
                qa_entry_id TEXT PRIMARY KEY, ticket_seq INTEGER NOT NULL UNIQUE REFERENCES tickets(seq),
                answer TEXT NOT NULL, doc_id TEXT, section TEXT, directorate TEXT, stage TEXT,
                status TEXT NOT NULL DEFAULT 'draft', confirmed_by TEXT, confirmed_at TEXT,
                is_demo INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS quality_mail (
                ticket_seq INTEGER PRIMARY KEY REFERENCES tickets(seq),
                retry_at INTEGER NOT NULL DEFAULT 0,
                sent_at INTEGER
            );
        """)
    mail_worker = asyncio.create_task(deliver_mail(database)) if test_mail_enabled() else None
    try:
        yield
    finally:
        if mail_worker:
            mail_worker.cancel()
            with suppress(asyncio.CancelledError):
                await mail_worker


def test_mail_enabled():
    return os.environ.get("PUNKT_TEST_MAIL") == "1"


app = FastAPI(title="Пункт · локальный API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


class LoginRequest(BaseModel):
    user_id: str


class ChatRequest(BaseModel):
    session_id: str
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]


class TicketAnswer(BaseModel):
    answer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]
    doc_id: str | None = None
    section: str | None = None
    directorate: str | None = None
    stage: str | None = None


def current_user(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    user_id = tokens.get(credentials.credentials) if credentials else None
    if user_id is None:
        raise HTTPException(401, "Войдите в тестовый аккаунт.", headers={"WWW-Authenticate": "Bearer"})
    return USERS[user_id]


User = Annotated[dict, Depends(current_user)]


def require_quality(user: User):
    if user["role"] != "quality":
        raise HTTPException(403, "Очередь доступна службе качества.")


def require_session(connection, session_id, user_id):
    session = connection.execute(
        "SELECT session_id FROM sessions WHERE session_id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()
    if session is None:
        raise HTTPException(404, "Сессия не найдена.")


@app.get("/health")
def health():
    with database() as connection:
        connection.execute("SELECT 1")
    real_mode = os.environ.get("AGENT_MODE") == "real"
    llm_ready = real_mode and model_ready()
    search_ready = real_mode and document_search_enabled() and document_corpus_available()
    return {"db": "ok", "search": "ok" if search_ready else "down", "llm": "ok" if llm_ready else "down"}


@app.get("/auth/users")
def users():
    return list(USERS.values())


@app.post("/auth/login")
def login(payload: LoginRequest):
    user = USERS.get(payload.user_id)
    if user is None:
        raise HTTPException(404, "Тестовый аккаунт не найден.")
    token = secrets.token_urlsafe(32)
    tokens[token] = payload.user_id
    return {"token": token, "user": user}


@app.get("/auth/me")
def me(user: User):
    return user


@app.post("/chat/sessions")
def create_session(user: User):
    session_id = uuid4().hex
    with database() as connection:
        connection.execute(
            "INSERT INTO sessions (session_id, user_id, created_at) VALUES (?, ?, ?)",
            (session_id, user["user_id"], datetime.now(UTC).isoformat()),
        )
    return {"session_id": session_id}


@app.get("/chat/sessions")
def sessions(user: User):
    with database() as connection:
        rows = connection.execute(
            "SELECT session_id, created_at FROM sessions WHERE user_id = ? ORDER BY created_at DESC",
            (user["user_id"],),
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/chat/sessions/{session_id}")
def session_messages(session_id: str, user: User):
    with database() as connection:
        require_session(connection, session_id, user["user_id"])
        rows = connection.execute(
            "SELECT message_id, role, content, status, result FROM messages WHERE session_id = ? ORDER BY seq",
            (session_id,),
        ).fetchall()
    messages = [dict(row) for row in rows]
    for message in messages:
        if message["result"] is not None:
            message["result"] = json.loads(message["result"])
    return {"session_id": session_id, "messages": messages}


def create_ticket(connection, user_id, session_id, question, reason):
    normalized = normalized_question(question)
    dedup_key = f"{session_id}:{normalized}"
    cursor = connection.execute(
        """INSERT INTO tickets (session_id, user_id, created_at, question, context, dedup_key)
           VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (dedup_key) DO NOTHING""",
        (session_id, user_id, datetime.now(UTC).isoformat(), question, json.dumps({"reason": reason}), dedup_key),
    )
    seq = connection.execute("SELECT seq FROM tickets WHERE dedup_key = ?", (dedup_key,)).fetchone()["seq"]
    ticket_id = f"Q-{seq:04d}"
    created = cursor.rowcount == 1
    if test_mail_enabled():
        connection.execute(
            """INSERT OR IGNORE INTO quality_mail (ticket_seq)
               SELECT seq FROM tickets WHERE seq = ? AND status IN ('open', 'answered')""",
            (seq,),
        )
    message = f"Обращение {ticket_id} {'создано' if created else 'уже существует'} в очереди службы качества."
    if test_mail_enabled():
        mail = connection.execute("SELECT sent_at FROM quality_mail WHERE ticket_seq = ?", (seq,)).fetchone()
        if mail is not None:
            delivery = "отправлено" if mail["sent_at"] is not None else "поставлено в очередь на отправку"
            message += f" Письмо {delivery}, {employee_address(user_id)} в копии."
    return {"ticket_id": ticket_id, "created": created, "message": message}


def ask_and_save(payload, user):
    session_id, question = payload.session_id, payload.question
    with database() as connection:
        require_session(connection, session_id, user["user_id"])
        rows = connection.execute(
            "SELECT seq, role, content, status FROM messages WHERE session_id = ? ORDER BY seq DESC LIMIT 12",
            (session_id,),
        ).fetchall()
        history = [dict(row) for row in reversed(rows)]
        last_seq = rows[0]["seq"] if rows else None
        confirmed_entries = knowledge_entries(connection, status="confirmed")
    real_mode = os.environ.get("AGENT_MODE") == "real"
    run = run_agent if real_mode else run_mock
    try:
        result = run(question, history, confirmed_entries)
    except AgentError as exc:
        raise HTTPException(502, str(exc)) from exc

    with database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current_seq = connection.execute(
            "SELECT MAX(seq) FROM messages WHERE session_id = ?", (session_id,),
        ).fetchone()[0]
        if current_seq != last_seq:
            raise HTTPException(409, "В этом чате уже появился другой ответ. Повторите запрос.")
        if result["status"] == "escalated":
            ticket_question = question
            if real_mode:
                original, clarifications = conversation_question(question, history)
                ticket_question = "\n".join([original, *(f"Уточнение: {item['reply']}" for item in clarifications)])
            result["ticket"] = create_ticket(
                connection, user["user_id"], session_id, ticket_question, result["reason"],
            )
            result["answer"] += " " + result["ticket"]["message"]
        result["message_id"] = uuid4().hex
        connection.executemany(
            "INSERT INTO messages (message_id, session_id, role, content, status, result) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (uuid4().hex, session_id, "user", question, None, None),
                (result["message_id"], session_id, "assistant", result["answer"], result["status"], json.dumps(result)),
            ],
        )
    return result


@app.post("/chat/ask")
def ask(payload: ChatRequest, user: User):
    return ask_and_save(payload, user)


@app.post("/chat/ask/stream")
def ask_stream(payload: ChatRequest, user: User):
    result = ask_and_save(payload, user)

    def events() -> Iterator[str]:
        for step in result["steps"]:
            yield f"event: step\ndata: {json.dumps(step, ensure_ascii=False)}\n\n"
        yield f"event: result\ndata: {json.dumps(result, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/tickets", dependencies=[Depends(require_quality)])
def tickets(status: str = "open"):
    with database() as connection:
        rows = connection.execute(
            "SELECT seq, session_id, user_id, created_at, question, context, status, dedup_key "
            "FROM tickets WHERE status = ? ORDER BY seq DESC",
            (status,),
        ).fetchall()
    return [serialize_ticket(row) for row in rows]


def serialize_ticket(row):
    ticket = dict(row)
    ticket["ticket_id"] = f"Q-{ticket.pop('seq'):04d}"
    ticket["context"] = json.loads(ticket["context"])
    return ticket


def ticket_row(connection, ticket_id):
    prefix, separator, number = ticket_id.partition("-")
    if prefix == "Q" and separator and number.isascii() and number.isdecimal() and len(number) < 19:
        row = connection.execute("SELECT * FROM tickets WHERE seq = ?", (int(number),)).fetchone()
        if row is not None:
            return row
    raise HTTPException(404, "Обращение не найдено.")


def knowledge_entries(connection, *, status=None, entry_id=None, ticket_seq=None):
    rows = connection.execute(
        """SELECT qa.*, tickets.question FROM qa_entries AS qa JOIN tickets ON qa.ticket_seq = tickets.seq
           WHERE (? IS NULL OR qa.status = ?) AND (? IS NULL OR qa.qa_entry_id = ?)
             AND (? IS NULL OR qa.ticket_seq = ?)
           ORDER BY qa.confirmed_at DESC, qa.ticket_seq DESC""",
        (status, status, entry_id, entry_id, ticket_seq, ticket_seq),
    ).fetchall()
    entries = [dict(row) for row in rows]
    for entry in entries:
        entry["ticket_id"] = f"Q-{entry.pop('ticket_seq'):04d}"
        entry["is_demo"] = bool(entry["is_demo"])
    return entries


@app.get("/tickets/{ticket_id}", dependencies=[Depends(require_quality)])
def ticket_detail(ticket_id: str):
    with database() as connection:
        row = ticket_row(connection, ticket_id)
        entries = knowledge_entries(connection, ticket_seq=row["seq"])
        mail = connection.execute(
            "SELECT sent_at FROM quality_mail WHERE ticket_seq = ?", (row["seq"],),
        ).fetchone() if test_mail_enabled() else None
    return {**serialize_ticket(row), "qa_entry": entries[0] if entries else None, "test_mail": mail is not None}


@app.post("/tickets/{ticket_id}/answer", dependencies=[Depends(require_quality)])
def save_ticket_answer(ticket_id: str, payload: TicketAnswer):
    with database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        ticket = ticket_row(connection, ticket_id)
        if ticket["status"] in ("published", "rejected"):
            raise HTTPException(409, "Ответ по этому обращению уже закрыт для редактирования.")
        connection.execute(
            """INSERT INTO qa_entries (qa_entry_id, ticket_seq, answer, doc_id, section, directorate, stage)
               VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (ticket_seq) DO UPDATE SET
               answer=excluded.answer, doc_id=excluded.doc_id, section=excluded.section,
               directorate=excluded.directorate, stage=excluded.stage""",
            (uuid4().hex, ticket["seq"], payload.answer, payload.doc_id, payload.section, payload.directorate, payload.stage),
        )
        connection.execute("UPDATE tickets SET status = 'answered' WHERE seq = ?", (ticket["seq"],))
        return knowledge_entries(connection, ticket_seq=ticket["seq"])[0]


@app.post("/tickets/{ticket_id}/test-mail-reply", dependencies=[Depends(require_quality)])
def test_mail_reply(ticket_id: str):
    if not test_mail_enabled():
        raise HTTPException(404, "Тестовая почта выключена.")

    with database() as connection:
        ticket = ticket_row(connection, ticket_id)
        entries = knowledge_entries(connection, ticket_seq=ticket["seq"])
        if not entries or ticket["status"] == "rejected":
            raise HTTPException(409, "Сохраните черновик ответа для этого обращения.")
        entry = entries[0]
        if entry["status"] == "confirmed":
            return entry
        mail = connection.execute(
            "SELECT sent_at FROM quality_mail WHERE ticket_seq = ?", (ticket["seq"],),
        ).fetchone()
    if mail is None or mail["sent_at"] is None:
        raise HTTPException(409, "Письмо по обращению ещё отправляется. Попробуйте ещё раз через несколько секунд.")

    try:
        send_demo_reply(ticket, entry["answer"])
        receive_replies(database, set())
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(503, "Тестовая почта временно недоступна. Проверьте обращение перед повторной отправкой.") from exc

    with database() as connection:
        entry = knowledge_entries(connection, ticket_seq=ticket["seq"])[0]
    if entry["status"] != "confirmed":
        raise HTTPException(503, "Ответ ушёл в тестовую почту. Обновите обращение, чтобы увидеть результат обработки.")
    return entry


@app.post("/qa/{entry_id}/confirm", dependencies=[Depends(require_quality)])
def confirm_answer(entry_id: str, user: User):
    with database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT status, ticket_seq FROM qa_entries WHERE qa_entry_id = ?", (entry_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Черновик ответа не найден.")
        if row["status"] != "confirmed":
            connection.execute(
                "UPDATE qa_entries SET status = 'confirmed', confirmed_by = ?, confirmed_at = ? WHERE qa_entry_id = ?",
                (user["user_id"], datetime.now(UTC).isoformat(), entry_id),
            )
            connection.execute("UPDATE tickets SET status = 'published' WHERE seq = ?", (row["ticket_seq"],))
        return knowledge_entries(connection, entry_id=entry_id)[0]


@app.get("/qa")
def published_answers(_user: User):
    with database() as connection:
        return knowledge_entries(connection, status="confirmed")
