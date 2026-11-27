import asyncio
import json
import logging
import os
import secrets
import sqlite3
import threading
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, closing, contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, StringConstraints

from app.agent import (
    AgentError,
    conversation_question,
    document_search_enabled,
    document_search_ready,
    model_ready,
    run_agent,
)
from app.mocks.fake_agent import run_mock
from app.mocks.knowledge import normalized_question
from app.quality_mail import deliver_mail, mailpit_password, queue_confirmed_reply, receive_replies, send_demo_reply


logger = logging.getLogger(__name__)

USERS = {
    "employee": {"user_id": "employee", "name": "Сотрудник", "role": "employee"},
    "quality": {"user_id": "quality", "name": "Служба качества", "role": "quality"},
}
DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "processed" / "mock.sqlite3"
bearer = HTTPBearer(auto_error=False)
# token → (аккаунт, владелец истории, срок). У общего демо-аккаунта сотрудника у каждого браузера своя
# история: владелец — «employee-<visitor_id>», иначе жюри видит вопросы друг друга.
tokens: dict[str, tuple[str, str, float]] = {}
TOKEN_TTL = 8 * 60 * 60
VISITOR_ID = r"^[a-z0-9]{8,32}$"

MAX_BODY_BYTES = 64 * 1024  # вопрос ≤ 8000 символов ≈ 16 КБ в UTF-8; всё сверх — мусор или атака
EXAMPLES = {
    "documents": [
        # Формулировка проверена на Qwen 3 из 3; «Кто назначает руководителя проекта…» модель иногда
        # возвращает с несуществующим номером источника, и агент отбрасывает ответ (см. run_agent).
        {"title": "Ответ с источником", "question": "Кто назначает РП в дирекции ИБ?"},
        {"title": "Уточнение по дирекции", "question": "Что будет, если по заявке не хватает информации?"},
        {"title": "Передача в службу качества", "question": "Сколько дней даётся на согласование ТКП с юристами?"},
    ],
    "demo": [
        {"title": "Ответ с источником", "question": "Кто проверяет тестовую заявку?"},
        {"title": "Уточнение по дирекции", "question": "Что будет, если по заявке не хватает информации?"},
        {"title": "Передача в службу качества", "question": "Сколько дней даётся на согласование ТКП с юристами?"},
    ],
}


class RateLimiter:
    """Скользящее окно в памяти процесса: ключ → времена последних запросов."""

    def __init__(self):
        self.hits: defaultdict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, key: str, limit: int, window: float) -> None:
        now = monotonic()
        with self.lock:
            hits = self.hits[key]
            while hits and hits[0] <= now - window:
                hits.popleft()
            if len(hits) >= limit:
                retry = int(hits[0] + window - now) + 1
                raise HTTPException(429, f"Слишком много запросов. Повторите через {retry} с.",
                                    headers={"Retry-After": str(retry)})
            hits.append(now)
            if len(self.hits) > 10_000:
                for stale in [k for k, v in self.hits.items() if not v or v[-1] <= now - window]:
                    del self.hits[stale]


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    # За обратным прокси uvicorn подставляет адрес из X-Forwarded-For (FORWARDED_ALLOW_IPS в compose).
    return request.client.host if request.client else "unknown"


def limit(name: str, count: int, window: float):
    def dependency(request: Request):
        limiter.check(f"{name}:{client_ip(request)}", count, window)
    return dependency


class LlmQueue:
    """Очередь к модели: Ollama по умолчанию отвечает по одному, и без очереди в приложении ожидание
    засчитывалось в таймаут агента — шестой одновременный вопрос получал 502 через 120 с."""

    def __init__(self):
        self.parallel = max(1, int(os.environ.get("PUNKT_LLM_PARALLEL", "1")))
        self.wait = float(os.environ.get("PUNKT_QUEUE_WAIT_S", "300"))
        self.slots = threading.BoundedSemaphore(self.parallel)
        self.lock = threading.Lock()
        self.pending = 0
        self.sessions: set[str] = set()

    @contextmanager
    def turn(self, session_id: str, notify=None):
        with self.lock:
            if session_id in self.sessions:
                raise HTTPException(409, "В этом чате уже готовится ответ. Дождитесь его.")
            self.sessions.add(session_id)
            ahead = max(0, self.pending - self.parallel + 1)
            self.pending += 1
        try:
            if ahead and notify:
                notify({"title": f"Жду очереди к модели, вопросов впереди: {ahead}"})
            if not self.slots.acquire(timeout=self.wait):
                raise HTTPException(503, "Сейчас много вопросов. Повторите через минуту.")
            try:
                if ahead and notify:
                    notify({"title": "Модель готовит ответ…"})
                yield
            finally:
                self.slots.release()
        finally:
            with self.lock:
                self.pending -= 1
                self.sessions.discard(session_id)


llm_queue = LlmQueue()


def account_password(user_id: str) -> str:
    return os.environ.get(f"PUNKT_{user_id.upper()}_PASSWORD", "")


def validate_account_passwords():
    employee, quality = (account_password(user_id) for user_id in USERS)
    if not employee.strip() or not quality.strip() or employee == quality:
        raise RuntimeError("Set distinct passwords for both Punkt demo accounts.")


@contextmanager
def database():
    database_path = Path(os.environ.get("KESHAKING_DB_PATH", str(DEFAULT_DB)))
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database_path, timeout=10)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        yield connection


def migrate_quality_mail(connection):
    connection.execute("BEGIN IMMEDIATE")
    if "reply_token" not in {column["name"] for column in connection.execute("PRAGMA table_info(quality_mail)")}:
        connection.execute("ALTER TABLE quality_mail ADD COLUMN reply_token TEXT")
        connection.execute(
            """INSERT INTO quality_mail_copy (ticket_seq, sent_at)
               SELECT ticket_seq, sent_at FROM quality_mail"""
        )

    # Older installations trusted the From header to publish. Keep those answers for manual review.
    mail_confirmations = "SELECT ticket_seq FROM qa_entries WHERE confirmed_by = 'quality-mail'"
    connection.execute(f"DELETE FROM quality_reply_mail WHERE ticket_seq IN ({mail_confirmations})")
    connection.execute(f"UPDATE tickets SET status = 'answered' WHERE seq IN ({mail_confirmations})")
    connection.execute(
        """UPDATE qa_entries SET status = 'draft', confirmed_by = NULL, confirmed_at = NULL
           WHERE confirmed_by = 'quality-mail'"""
    )
    old_threads = connection.execute(
        """SELECT m.ticket_seq FROM quality_mail AS m JOIN tickets AS t ON t.seq = m.ticket_seq
           WHERE m.reply_token IS NULL AND t.status IN ('open', 'answered')"""
    ).fetchall()
    connection.executemany(
        "UPDATE quality_mail SET reply_token = ?, sent_at = NULL, retry_at = 0 WHERE ticket_seq = ?",
        ((secrets.token_hex(32), row["ticket_seq"]) for row in old_threads),
    )


@asynccontextmanager
async def lifespan(_app):
    validate_account_passwords()
    if test_mail_enabled() and mailpit_password() in {account_password(user_id) for user_id in USERS}:
        raise RuntimeError("Set a separate password for Mailpit.")
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
                reply_token TEXT,
                retry_at INTEGER NOT NULL DEFAULT 0,
                sent_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS quality_mail_copy (
                ticket_seq INTEGER PRIMARY KEY REFERENCES tickets(seq),
                retry_at INTEGER NOT NULL DEFAULT 0,
                sent_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS quality_reply_mail (
                ticket_seq INTEGER PRIMARY KEY REFERENCES tickets(seq),
                retry_at INTEGER NOT NULL DEFAULT 0,
                sent_at INTEGER
            );
        """)
        migrate_quality_mail(connection)
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


class BodyLimit:
    """413 для тел больше MAX_BODY_BYTES — до разбора JSON (иначе 400 МБ без входа съедали память контейнера)."""

    def __init__(self, app, max_bytes: int = MAX_BODY_BYTES):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            return await self.reject(scope, receive, send)
        body, more = b"", True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            more = message.get("more_body", False)
            if len(body) > self.max_bytes:
                return await self.reject(scope, receive, send)
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    @staticmethod
    async def reject(scope, receive, send):
        await JSONResponse({"detail": "Запрос слишком большой."}, status_code=413)(scope, receive, send)


SECURITY_HEADERS = [
    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                                 b"connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
    (b"x-frame-options", b"DENY"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"same-origin"),
]


class SecurityHeaders:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def with_headers(message):
            if message["type"] == "http.response.start":
                present = {name for name, _ in message.get("headers", [])}
                message.setdefault("headers", [])
                message["headers"] = [*message["headers"], *(h for h in SECURITY_HEADERS if h[0] not in present)]
            await send(message)

        await self.app(scope, receive, with_headers)


api_docs = os.environ.get("PUNKT_API_DOCS") == "1"  # схема API наружу только по явному флагу
app = FastAPI(
    title="Пункт · локальный API", lifespan=lifespan,
    docs_url="/docs" if api_docs else None, redoc_url="/redoc" if api_docs else None,
    openapi_url="/openapi.json" if api_docs else None,
)
app.add_middleware(SecurityHeaders)
app.add_middleware(BodyLimit)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


class LoginRequest(BaseModel):
    user_id: Annotated[str, StringConstraints(max_length=64)]
    password: Annotated[str, StringConstraints(max_length=1024)] = ""
    visitor_id: Annotated[str, StringConstraints(pattern=VISITOR_ID)] | None = None


class ChatRequest(BaseModel):
    session_id: str
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]


class TicketAnswer(BaseModel):
    answer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]
    doc_id: str | None = None
    section: str | None = None
    directorate: str | None = None
    stage: str | None = None


Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


def current_user(credentials: Credentials):
    if credentials:
        token = credentials.credentials
        session = tokens.get(token)
        if session and session[2] > monotonic():
            account, owner, _ = session
            return {**USERS[account], "user_id": owner}
        tokens.pop(token, None)
    raise HTTPException(401, "Войдите в аккаунт.", headers={"WWW-Authenticate": "Bearer"})


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
        connection.execute("SELECT 1 FROM sessions LIMIT 1").fetchone()
    real_mode = os.environ.get("AGENT_MODE") == "real"
    llm_ready = real_mode and model_ready()
    search_ready = real_mode and document_search_enabled() and document_search_ready()
    return {"db": "ok", "search": "ok" if search_ready else "down", "llm": "ok" if llm_ready else "down"}


@app.get("/ready")
def readiness():
    try:
        checks = health()
    except (sqlite3.Error, OSError):
        return JSONResponse({"db": "down"}, status_code=503)
    required = ["db"]
    if os.environ.get("AGENT_MODE") == "real":
        required.append("llm")
        if document_search_enabled():
            required.append("search")
    ready = all(checks[dependency] == "ok" for dependency in required)
    return JSONResponse(checks, status_code=200 if ready else 503)


@app.get("/auth/users")
def users():
    return [{**user, "password_required": True} for user in USERS.values()]


@app.post("/auth/login", dependencies=[Depends(limit("login", 10, 60))])
def login(payload: LoginRequest):
    user = USERS.get(payload.user_id)
    if user is None:
        raise HTTPException(404, "Тестовый аккаунт не найден.")
    password = account_password(payload.user_id)
    if not password.strip() or not secrets.compare_digest(password.encode(), payload.password.encode()):
        raise HTTPException(401, "Неверный пароль.")
    now = monotonic()
    for key, (_, _, expiry) in tokens.copy().items():
        if expiry <= now:
            tokens.pop(key, None)
    owner = payload.user_id
    if user["role"] == "employee" and payload.visitor_id:
        owner = f"{payload.user_id}-{payload.visitor_id}"
    token = secrets.token_urlsafe(32)
    tokens[token] = (payload.user_id, owner, now + TOKEN_TTL)
    return {"token": token, "user": {**user, "user_id": owner}}


@app.get("/auth/me")
def me(user: User):
    return user


@app.post("/auth/logout")
def logout(credentials: Credentials):
    if credentials:
        tokens.pop(credentials.credentials, None)
    return {"ok": True}


@app.get("/chat/examples")
def examples(_user: User):
    """Примеры на пустом экране зависят от источников: учебные вопросы на настоящих регламентах не работают."""
    return EXAMPLES["documents" if document_search_enabled() else "demo"]


@app.post("/chat/sessions", dependencies=[Depends(limit("sessions", 30, 60))])
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
            """SELECT s.session_id, s.created_at, (
                   SELECT substr(m.content, 1, 100) FROM messages m
                   WHERE m.session_id = s.session_id AND m.role = 'user' ORDER BY m.seq LIMIT 1
               ) AS title FROM sessions s
               WHERE s.user_id = ? AND EXISTS (SELECT 1 FROM messages m WHERE m.session_id = s.session_id)
               ORDER BY s.created_at DESC""",
            (user["user_id"],),
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/chat/sessions/{session_id}")
def session_messages(session_id: str, user: User):
    with database() as connection:
        connection.execute("BEGIN")
        require_session(connection, session_id, user["user_id"])
        rows = connection.execute(
            "SELECT message_id, role, content, status, result FROM messages WHERE session_id = ? ORDER BY seq",
            (session_id,),
        ).fetchall()
        replies = connection.execute(
            """SELECT q.qa_entry_id, q.answer, t.seq AS ticket_seq
               FROM tickets AS t JOIN qa_entries AS q ON q.ticket_seq = t.seq
               WHERE t.session_id = ? AND t.status = 'published' AND q.status = 'confirmed'
               ORDER BY q.confirmed_at, t.seq""",
            (session_id,),
        ).fetchall()
    replies_by_ticket = {
        f"Q-{reply['ticket_seq']:04d}": {
            "message_id": f"quality-{reply['qa_entry_id']}",
            "role": "quality",
            "content": reply["answer"],
            "ticket_id": f"Q-{reply['ticket_seq']:04d}",
        }
        for reply in replies
    }
    messages = []
    for row in rows:
        message = dict(row)
        if message["result"] is not None:
            message["result"] = json.loads(message["result"])
        messages.append(message)
        ticket = (message["result"] or {}).get("ticket")
        if ticket and (reply := replies_by_ticket.pop(ticket["ticket_id"], None)) is not None:
            messages.append(reply)
    messages.extend(replies_by_ticket.values())
    return {"session_id": session_id, "messages": messages}


def create_ticket(connection, user_id, session_id, question, reason, *, conflict_sources=()):
    normalized = normalized_question(question)
    dedup_key = f"{session_id}:{normalized}"
    context = {"reason": reason}
    if conflict_sources:
        source_ids = sorted(source["chunk_id"] for source in conflict_sources)
        dedup_key = json.dumps([session_id, normalized, source_ids])
        context["conflict_sources"] = conflict_sources
    cursor = connection.execute(
        """INSERT INTO tickets (session_id, user_id, created_at, question, context, dedup_key)
           VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (dedup_key) DO NOTHING""",
        (session_id, user_id, datetime.now(UTC).isoformat(), question, json.dumps(context), dedup_key),
    )
    seq = connection.execute("SELECT seq FROM tickets WHERE dedup_key = ?", (dedup_key,)).fetchone()["seq"]
    ticket_id = f"Q-{seq:04d}"
    created = cursor.rowcount == 1
    if test_mail_enabled():
        connection.execute(
            """INSERT OR IGNORE INTO quality_mail (ticket_seq, reply_token)
               SELECT seq, ? FROM tickets WHERE seq = ? AND status IN ('open', 'answered')""",
            (secrets.token_hex(32), seq),
        )
        connection.execute(
            """INSERT OR IGNORE INTO quality_mail_copy (ticket_seq)
               SELECT ticket_seq FROM quality_mail WHERE ticket_seq = ?""",
            (seq,),
        )
    message = f"Обращение {ticket_id} {'создано' if created else 'уже существует'} в очереди службы качества."
    if test_mail_enabled():
        mail = connection.execute("SELECT sent_at FROM quality_mail WHERE ticket_seq = ?", (seq,)).fetchone()
        if mail is not None:
            delivery = "отправлено" if mail["sent_at"] is not None else "поставлено в очередь на отправку"
            message += f" Письмо службе качества {delivery}. Сотрудник получит отдельное уведомление."
    return {"ticket_id": ticket_id, "created": created, "message": message}


def ask_and_save(payload, user, notify=None):
    session_id, question = payload.session_id, payload.question
    with database() as connection:
        require_session(connection, session_id, user["user_id"])
    with llm_queue.turn(session_id, notify):
        return answer_and_save(session_id, question, user)


def answer_and_save(session_id, question, user):
    with database() as connection:
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
        conflict_sources = []
        if result["status"] == "conflict":
            source_types = {source["source_type"] for source in result["citations"]}
            if {"regulation", "qa_entry"} <= source_types:
                conflict_sources = [
                    {key: source[key] for key in ("chunk_id", "doc_title", "section", "source_type")}
                    for source in result["citations"]
                ]
        if result["status"] == "escalated" or conflict_sources:
            ticket_question = question
            if real_mode:
                original, clarifications = conversation_question(question, history)
                ticket_question = "\n".join([original, *(f"Уточнение: {item['reply']}" for item in clarifications)])
            result["ticket"] = create_ticket(
                connection, user["user_id"], session_id, ticket_question, result["reason"],
                conflict_sources=conflict_sources,
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


ASK_LIMIT = Depends(limit("ask", 10, 60))
KEEPALIVE_S = 10


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/chat/ask", dependencies=[ASK_LIMIT])
def ask(payload: ChatRequest, user: User):
    return ask_and_save(payload, user)


@app.post("/chat/ask/stream", dependencies=[ASK_LIMIT])
async def ask_stream(payload: ChatRequest, user: User):
    """Поток открывается сразу: шаг «ищу», место в очереди и комментарии keep-alive раз в 10 с.
    Раньше сервер молчал до готового ответа (20–120 с), и прокси с таймаутом 60 с обрывал соединение."""
    with database() as connection:
        require_session(connection, payload.session_id, user["user_id"])
    loop = asyncio.get_running_loop()
    updates: asyncio.Queue = asyncio.Queue()

    def notify(step):
        loop.call_soon_threadsafe(updates.put_nowait, step)

    work = asyncio.ensure_future(asyncio.to_thread(ask_and_save, payload, user, notify))

    async def events() -> AsyncIterator[str]:
        yield sse("step", {"title": "Ищу пункты в регламентах…"})
        waiting = None
        while True:
            waiting = waiting or asyncio.ensure_future(updates.get())
            done, _ = await asyncio.wait({work, waiting}, timeout=KEEPALIVE_S, return_when=asyncio.FIRST_COMPLETED)
            if waiting in done:
                yield sse("step", waiting.result())
                waiting = None
            elif work in done:
                waiting.cancel()
                break
            else:
                yield ": keep-alive\n\n"
        try:
            result = work.result()
        except HTTPException as exc:
            yield sse("error", {"status": exc.status_code, "message": exc.detail})
            return
        except Exception:
            logger.exception("Ответ на вопрос не сохранён")
            yield sse("error", {"status": 500, "message": "Не удалось получить ответ. Повторите запрос."})
            return
        for step in result["steps"]:
            yield sse("step", step)
        yield sse("result", result)

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


@app.get("/my/tickets")
def my_tickets(user: User):
    """Обращения сотрудника в службу качества: статус и опубликованный ответ (черновик эксперта не показываем)."""
    with database() as connection:
        rows = connection.execute(
            """SELECT t.seq, t.session_id, t.created_at, t.question, t.status,
                      CASE WHEN t.status = 'published' THEN qa.answer END AS answer,
                      CASE WHEN t.status = 'published' THEN qa.doc_id END AS doc_id,
                      CASE WHEN t.status = 'published' THEN qa.section END AS section
               FROM tickets t LEFT JOIN qa_entries qa ON qa.ticket_seq = t.seq
               WHERE t.user_id = ? ORDER BY t.seq DESC""",
            (user["user_id"],),
        ).fetchall()
    result = []
    for row in rows:
        ticket = dict(row)
        ticket["ticket_id"] = f"Q-{ticket.pop('seq'):04d}"
        result.append(ticket)
    return result


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
def test_mail_reply(ticket_id: str, payload: TicketAnswer):
    if not test_mail_enabled():
        raise HTTPException(404, "Тестовая почта выключена.")

    with database() as connection:
        ticket = ticket_row(connection, ticket_id)
        if ticket["status"] != "open":
            raise HTTPException(409, "По обращению уже есть черновик или опубликованный ответ.")
        mail = connection.execute(
            "SELECT sent_at, reply_token FROM quality_mail WHERE ticket_seq = ?", (ticket["seq"],),
        ).fetchone()
    if mail is None or mail["sent_at"] is None or mail["reply_token"] is None:
        raise HTTPException(409, "Письмо по обращению ещё отправляется. Попробуйте ещё раз через несколько секунд.")

    try:
        send_demo_reply(ticket["seq"], mail["reply_token"], payload.answer)
        receive_replies(database, set())
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(503, "Тестовая почта временно недоступна. Обновите обращение перед повторной отправкой.") from exc

    with database() as connection:
        entries = knowledge_entries(connection, ticket_seq=ticket["seq"], status="draft")
    if not entries:
        raise HTTPException(409, "Обновите обращение, чтобы увидеть результат обработки письма.")
    return entries[0]


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
            if test_mail_enabled():
                queue_confirmed_reply(connection, row["ticket_seq"])
        return knowledge_entries(connection, entry_id=entry_id)[0]


@app.get("/qa", dependencies=[Depends(require_quality)])
def published_answers(_user: User):
    with database() as connection:
        return knowledge_entries(connection, status="confirmed")
