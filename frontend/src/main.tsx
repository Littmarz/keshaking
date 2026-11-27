import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { createRoot } from 'react-dom/client';
import { ApiError, askQuestion, request, type Account, type Answer, type AuthSession, type Citation, type Message, type Session } from './api';
import { QualityDesk } from './QualityDesk';
import { SignIn } from './SignIn';
import './styles.css';

const examples = [
  { title: 'Ответ с источником', question: 'Кто проверяет тестовую заявку?' },
  { title: 'Уточнение по дирекции', question: 'Что будет, если по заявке не хватает информации?' },
  { title: 'Передача в службу качества', question: 'Сколько дней даётся на согласование ТКП с юристами?' },
];

const answerLabels = {
  answered: 'Ответ', partial: 'Частичный ответ', needs_clarification: 'Нужно уточнение',
  escalated: 'Служба качества', conflict: 'Источники расходятся', refused: 'Недостаточно данных',
};

function SourceDialog({ source, close }: { source: Citation; close: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { ref.current?.showModal(); }, []);
  return (
    <dialog ref={ref} className="source-dialog" onClose={close} aria-labelledby="source-title">
      <div className="source-inner">
        <button className="source-close" type="button" onClick={close} aria-label="Закрыть источник">×</button>
        <p className="eyebrow">{source.source_type === 'qa_entry' ? 'Учебное разъяснение' : `Пункт ${source.section}`}</p>
        <h2 id="source-title">{source.doc_title}</h2>
        {source.directorate && <p>{source.directorate}</p>}
        <blockquote>{source.quote}</blockquote>
      </div>
    </dialog>
  );
}

function AnswerCard({ result, busy, clarificationActive, ask, openSource, openQueue, ticketPublished }: {
  result: Answer; busy: boolean; clarificationActive: boolean; ask: (question: string) => void;
  openSource: (source: Citation) => void; openQueue?: (ticket: string) => void; ticketPublished: boolean;
}) {
  const ticketId = result.ticket?.ticket_id;
  return (
    <article className={`answer-card answer-${result.status}`} aria-label={answerLabels[result.status]}>
      <div className="answer-label"><span className="answer-dot" />{answerLabels[result.status]}</div>
      <p className="answer-text">
        {result.answer.split(/\[(\d+)\]/g).map((part, index) => {
          if (index % 2 === 0) return part;
          const source = result.citations[Number(part) - 1];
          return source ? <button className="citation-number" type="button" key={index}
            onClick={() => openSource(source)} aria-label={`Источник ${part}`}>{part}</button> : `[${part}]`;
        })}
      </p>
      {result.citations.length > 0 && <div className="source-links">
        {result.citations.map((source, index) => (
          <button className="source-link" type="button" key={`${source.chunk_id}-${index}`} onClick={() => openSource(source)}>
            <span>{index + 1}</span>{source.doc_title}<span className="arrow-up-right" aria-hidden="true" />
          </button>
        ))}
      </div>}
      {clarificationActive && result.clarification?.options?.length ? <div className="clarification-options">
        {result.clarification.options.map((option) => <button type="button" className="secondary" key={option}
          disabled={busy} onClick={() => ask(option)}>{option}</button>)}
      </div> : null}
      {ticketId && (openQueue ? (
        <button type="button" className="secondary" disabled={busy}
          onClick={() => openQueue(ticketId)}>Открыть обращение {ticketId}</button>
      ) : (
        <p className="ticket-status">Обращение {ticketId} · {ticketPublished ? 'Ответ получен' : 'Ожидает ответа'}</p>
      ))}
    </article>
  );
}

const tokenKey = 'punkt.auth-token';
const historyDate = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

function loadHistory(token: string, sessionId: string, signal?: AbortSignal) {
  return request<{ messages: Message[] }>(`/chat/sessions/${sessionId}`, token, undefined, signal);
}

function newSession(token: string, signal?: AbortSignal) {
  return request<{ session_id: string }>('/chat/sessions', token, {}, signal);
}

function Workspace({ auth, onExpired }: { auth: AuthSession; onExpired: (token: string) => void }) {
  const { token, user } = auth;
  const sessionKey = `punkt.selected-chat.${user.user_id}`;
  const isQuality = user.role === 'quality';
  const [screen, setScreen] = useState<'chat' | 'quality'>('chat');
  const [session, setSession] = useState('');
  const [sessions, setSessions] = useState<Session[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState('');
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [busy, setBusy] = useState(true);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [step, setStep] = useState('');
  const [error, setError] = useState('');
  const [historyOpen, setHistoryOpen] = useState(() => !window.matchMedia('(max-width: 720px)').matches);
  const [source, setSource] = useState<Citation | null>(null);
  const [initialTicket, setInitialTicket] = useState<string | null>(null);
  const thread = useRef<HTMLDivElement>(null);
  const publishedTickets = new Set(messages.flatMap((message) => message.role === 'quality' && message.ticket_id ? [message.ticket_id] : []));
  const hasPendingTicket = messages.some(({ result }) => result?.ticket && !publishedTickets.has(result.ticket.ticket_id));
  const lastMessage = messages.reduce<string | undefined>((last, message) => message.role === 'quality' ? last : message.message_id, undefined);

  const reportError = useCallback((cause: unknown, fallback: string) => {
    if (cause instanceof ApiError && cause.status === 401) {
      onExpired(token);
    } else {
      setError(cause instanceof Error ? cause.message : fallback);
    }
  }, [token, onExpired]);

  useEffect(() => {
    const controller = new AbortController();
    async function initialize() {
      try {
        const rows = await request<Session[]>('/chat/sessions', token, undefined, controller.signal);
        const previousId = sessionStorage.getItem(sessionKey);
        const selected = rows.find(({ session_id }) => session_id === previousId) ?? rows[0];
        if (!selected) return;
        const history = await loadHistory(token, selected.session_id, controller.signal);
        if (controller.signal.aborted) return;
        setSessions(rows);
        setSession(selected.session_id);
        setMessages(history.messages);
      } catch (cause) {
        if (!controller.signal.aborted) reportError(cause, 'История недоступна.');
      } finally {
        if (!controller.signal.aborted) { setBusy(false); setLoadingHistory(false); }
      }
    }
    void initialize();
    return () => controller.abort();
  }, [loadAttempt, token, sessionKey, reportError]);

  useEffect(() => {
    if (thread.current && screen === 'chat') thread.current.scrollTop = thread.current.scrollHeight;
  }, [messages, screen]);

  useEffect(() => {
    if (!session || busy || screen !== 'chat' || !hasPendingTicket) return;
    const controller = new AbortController();
    let timer: number;
    async function poll() {
      try {
        if (document.visibilityState !== 'hidden') {
          const history = await loadHistory(token, session, controller.signal);
          if (controller.signal.aborted) return;
          setMessages((current) => {
            const currentIds = new Set(current.filter(({ role }) => role === 'quality').map(({ message_id }) => message_id));
            const hasNewReply = history.messages.some(({ role, message_id }) => role === 'quality' && !currentIds.has(message_id));
            return hasNewReply ? history.messages : current;
          });
        }
      } catch (cause) {
        if (controller.signal.aborted) return;
        if (cause instanceof ApiError && cause.status === 401) { onExpired(token); return; }
      }
      timer = window.setTimeout(poll, 3000);
    }
    void poll();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [busy, screen, hasPendingTicket, onExpired, session, token]);

  function showChat(id: string, history: Message[]) {
    setSession(id);
    sessionStorage.setItem(sessionKey, id);
    setMessages(history);
    setQuestion('');
    setScreen('chat');
  }

  async function selectChat(id: string) {
    if (busy) return;
    if (id === session) { setScreen('chat'); return; }
    setBusy(true);
    setLoadingHistory(true);
    setError('');
    try {
      const history = await loadHistory(token, id);
      showChat(id, history.messages);
      if (window.matchMedia('(max-width: 720px)').matches) setHistoryOpen(false);
    } catch (cause) {
      reportError(cause, 'Не удалось открыть чат.');
      setScreen('chat');
    } finally {
      setBusy(false);
      setLoadingHistory(false);
    }
  }

  async function newChat() {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const created = await newSession(token);
      setSessions((items) => [{ ...created, created_at: new Date().toISOString() }, ...items]);
      showChat(created.session_id, []);
    } catch (cause) {
      reportError(cause, 'Чат не создался.');
      setScreen('chat');
    } finally {
      setBusy(false);
    }
  }

  async function send(value: string) {
    const text = value.trim();
    if (!text || busy) return;
    setBusy(true);
    setQuestion('');
    setError('');
    setStep('Получаю ответ…');
    const temporaryId = `pending-${Date.now()}`;
    setMessages((items) => [...items, { message_id: temporaryId, role: 'user', content: text }]);
    try {
      let id = session;
      if (!id) {
        const created = await newSession(token);
        id = created.session_id;
        setSession(id);
        sessionStorage.setItem(sessionKey, id);
        setSessions((items) => [{ ...created, created_at: new Date().toISOString() }, ...items]);
      }
      const result = await askQuestion(token, id, text, setStep);
      setMessages((items) => [...items, { message_id: result.message_id, role: 'assistant', content: result.answer, result }]);
      setSessions((items) => items.map((item) => item.session_id === id && !item.title ? { ...item, title: text } : item));
    } catch (cause) {
      reportError(cause, 'Не удалось получить ответ.');
      setMessages((items) => items.filter((item) => item.message_id !== temporaryId));
      setQuestion(text);
    } finally {
      setStep('');
      setBusy(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void send(question);
  }

  function shortcut(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void send(question);
    }
  }

  function showQueue(ticket: string | null = null) {
    setInitialTicket(ticket);
    setScreen('quality');
  }

  return (
    <>
      <div className="workspace">
        <aside className="sidebar">
          <nav aria-label="Разделы">
            <button type="button" className="nav-link" aria-current={screen === 'chat' ? 'page' : undefined}
              onClick={() => setScreen('chat')}><span className="nav-icon chat-icon" aria-hidden="true" />Задать вопрос</button>
            {isQuality && <button type="button" className="nav-link" aria-current={screen === 'quality' ? 'page' : undefined}
              onClick={() => showQueue()}><span className="nav-icon queue-icon" aria-hidden="true" />Служба качества</button>}
          </nav>
          <button className="new-chat" type="button" disabled={busy} onClick={() => void newChat()}><span aria-hidden="true">+</span> Новый чат</button>
          <details className="history" open={historyOpen} onToggle={(event) => setHistoryOpen(event.currentTarget.open)}>
            <summary>История чатов</summary>
            {sessions.length > 0 ? <ul>
              {sessions.map((item) => <li key={item.session_id}>
                <button type="button" disabled={busy} title={item.title || undefined}
                  aria-current={session === item.session_id && screen === 'chat' ? 'true' : undefined}
                  onClick={() => void selectChat(item.session_id)}>
                  <span>{item.title || 'Новый чат'}</span>
                  <time dateTime={item.created_at}>{historyDate.format(new Date(item.created_at))}</time>
                </button>
              </li>)}
            </ul> : <p>{loadingHistory ? 'Загружаю…' : 'Здесь появятся ваши чаты.'}</p>}
          </details>
        </aside>
        <main className="main">
          {isQuality && screen === 'quality' ? <QualityDesk token={token} onExpired={onExpired} initialTicket={initialTicket} /> : (
            <section className="chat" aria-labelledby="chat-heading">
              <div className="hero">
                <h1 id="chat-heading">Ответы по регламентам</h1>
                <p>Задайте вопрос о рабочем процессе. Если ответ есть в базе, вы увидите точный пункт.</p>
                <div className="hero-shape" aria-hidden="true"><span /><span /><span /></div>
              </div>
              <div className="chat-card card">
                {error && <p className="error" role="alert">{error}</p>}
                {loadingHistory && <p className="working" role="status">Загружаю чат…</p>}
                {error && !session && !busy && <button type="button" className="secondary" onClick={() => {
                  setError(''); setBusy(true); setLoadingHistory(true); setLoadAttempt((attempt) => attempt + 1);
                }}>Обновить историю</button>}
                {!loadingHistory && !messages.length && <div className="examples">
                  <h2>Примеры вопросов</h2>
                  <div className="example-grid">
                    {examples.map((example) => <button className="example" key={example.question} type="button"
                      disabled={busy} onClick={() => void send(example.question)}>
                      <strong>{example.title}</strong><span>{example.question}</span><span className="example-arrow arrow-up-right" aria-hidden="true" />
                    </button>)}
                  </div>
                </div>}
                {messages.length > 0 && <div ref={thread} className="thread" aria-live="polite">
                  {messages.map((message) => {
                    if (message.role === 'user') return <p key={message.message_id} className="user-message">{message.content}</p>;
                    if (message.role === 'quality') return (
                      <article key={message.message_id} className="answer-card quality-reply" aria-label="Ответ службы качества">
                        <div className="answer-label"><span className="answer-dot" />Ответ службы качества</div>
                        {message.ticket_id && <p className="ticket-status">Обращение {message.ticket_id}</p>}
                        <p className="answer-text">{message.content}</p>
                      </article>
                    );
                    return message.result ? (
                      <AnswerCard key={message.message_id} result={message.result} busy={busy}
                        clarificationActive={message.message_id === lastMessage}
                        ask={(value) => void send(value)} openSource={setSource} openQueue={isQuality ? showQueue : undefined}
                        ticketPublished={publishedTickets.has(message.result.ticket?.ticket_id ?? '')} />
                    ) : <p key={message.message_id}>{message.content}</p>;
                  })}
                </div>}
                {step && <p className="working" role="status"><span className="live-dot" />{step}</p>}
                <form className="composer" onSubmit={submit}>
                  <textarea aria-label="Ваш вопрос" value={question} onChange={(e) => setQuestion(e.target.value)} onKeyDown={shortcut}
                    placeholder="Например, кто согласует заявку?" rows={2} maxLength={8000} disabled={loadingHistory} />
                  <button className="primary send" disabled={!question.trim() || busy}>Спросить <span aria-hidden="true">↑</span></button>
                </form>
              </div>
            </section>
          )}
        </main>
      </div>
      {source && <SourceDialog source={source} close={() => setSource(null)} />}
    </>
  );
}

function App() {
  const [auth, setAuth] = useState<AuthSession | null>(null);
  const [restoring, setRestoring] = useState(() => Boolean(sessionStorage.getItem(tokenKey)));
  const [signingOut, setSigningOut] = useState(false);
  const [notice, setNotice] = useState('');

  const expire = useCallback((token: string) => {
    if (sessionStorage.getItem(tokenKey) !== token) return;
    sessionStorage.removeItem(tokenKey);
    setAuth(null);
    setNotice('Войдите снова, чтобы продолжить.');
  }, []);

  useEffect(() => {
    const token = sessionStorage.getItem(tokenKey);
    if (!token) return;
    const controller = new AbortController();
    async function restore(key: string) {
      try {
        const user = await request<Account>('/auth/me', key, undefined, controller.signal);
        if (!controller.signal.aborted) setAuth({ token: key, user });
      } catch {
        if (!controller.signal.aborted) expire(key);
      } finally {
        if (!controller.signal.aborted) setRestoring(false);
      }
    }
    void restore(token);
    return () => controller.abort();
  }, [expire]);

  async function signOut() {
    if (!auth || signingOut) return;
    setSigningOut(true);
    try {
      await request('/auth/logout', auth.token, {}, AbortSignal.timeout(5000));
    } catch {
      // Clear the browser session even if the server is unavailable.
    } finally {
      sessionStorage.removeItem(tokenKey);
      setAuth(null);
      setNotice('');
      setSigningOut(false);
    }
  }

  return (
    <div className="site">
      <header className="topbar">
        <div className="brand"><span className="brand-mark" aria-hidden="true">П</span><strong>Пункт</strong></div>
        {auth && <div className="account-bar">
          <span>{auth.user.name}</span>
          <button type="button" className="secondary" disabled={signingOut} onClick={() => void signOut()}>Выйти</button>
        </div>}
      </header>
      {restoring ? <main className="sign-in card" role="status">Проверяю вход…</main> : auth ? (
        <Workspace key={auth.token} auth={auth} onExpired={expire} />
      ) : <SignIn notice={notice} onSignedIn={(next) => {
        sessionStorage.setItem(tokenKey, next.token);
        setAuth(next);
        setNotice('');
      }} />}
      <footer className="build-footer">
        {import.meta.env.VITE_PUNKT_BUILD_VERSION
          ? `Сборка ${import.meta.env.VITE_PUNKT_BUILD_VERSION}`
          : 'Сборка без номера'}
      </footer>
    </div>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
