import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource/golos-text/400.css';
import '@fontsource/golos-text/500.css';
import '@fontsource/golos-text/600.css';
import '@fontsource/unbounded/400.css';
import '@fontsource/unbounded/500.css';
import '@fontsource/unbounded/600.css';
import '@fontsource/unbounded/700.css';
import '@fontsource/unbounded/800.css';
import { ApiError, askQuestion, request, type Account, type AuthSession, type Citation, type Message, type Session } from './api';
import { AnswerCard, QualityReply, SourceDialog } from './Answer';
import { assets } from './brand';
import { KnowledgeBase } from './KnowledgeBase';
import { MyRequests } from './MyRequests';
import { QualityDesk } from './QualityDesk';
import { Sidebar, type Screen } from './Sidebar';
import { SignIn } from './SignIn';
import { WorkspaceControls } from './WorkspaceControls';
import './styles.css';

type Example = { title: string; question: string };

const fallbackExamples: Example[] = [
  { title: 'Ответ с источником', question: 'Кто проверяет тестовую заявку?' },
  { title: 'Уточнение по дирекции', question: 'Что будет, если по заявке не хватает информации?' },
  { title: 'Передача в службу качества', question: 'Сколько дней даётся на согласование ТКП с юристами?' },
];

const tokenKey = 'punkt.auth-token';

function loadHistory(token: string, sessionId: string, signal?: AbortSignal) {
  return request<{ messages: Message[] }>(`/chat/sessions/${sessionId}`, token, undefined, signal);
}

function newSession(token: string, signal?: AbortSignal) {
  return request<{ session_id: string }>('/chat/sessions', token, {}, signal);
}

function Composer({ value, onChange, onSend, busy, disabled, placeholder, enterHint = false, autoFocus = false }: {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  busy: boolean;
  disabled?: boolean;
  placeholder: string;
  enterHint?: boolean;
  autoFocus?: boolean;
}) {
  const field = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const element = field.current;
    if (!element) return;
    element.style.height = 'auto';
    element.style.height = `${Math.min(element.scrollHeight, 180)}px`;
  }, [value]);
  useEffect(() => { if (autoFocus) field.current?.focus(); }, [autoFocus]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSend();
  }

  function shortcut(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      onSend();
    }
  }

  return (
    <form className="composer" onSubmit={submit}>
      <textarea ref={field} aria-label="Ваш вопрос" value={value} rows={1} maxLength={8000} placeholder={placeholder}
        disabled={disabled} onChange={(event) => onChange(event.target.value)} onKeyDown={shortcut} />
      {enterHint && <img className="enter-hint" src={assets.enter} width={32} height={32} alt="" title="Enter — отправить" />}
      <button className="send" type="submit" disabled={!value.trim() || busy} aria-label="Отправить вопрос">
        <img src={assets.sendArrow} width={17.25} height={11.05} alt="" />
      </button>
    </form>
  );
}

function Workspace({ auth, onExpired, onSignOut, signingOut }: {
  auth: AuthSession;
  onExpired: (token: string) => void;
  onSignOut: () => void;
  signingOut: boolean;
}) {
  const { token, user } = auth;
  const sessionKey = `punkt.selected-chat.${user.user_id}`;
  const isQuality = user.role === 'quality';
  const [screen, setScreen] = useState<Screen>('chat');
  const [session, setSession] = useState('');
  const [sessions, setSessions] = useState<Session[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState('');
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [busy, setBusy] = useState(true);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [step, setStep] = useState('');
  const [error, setError] = useState('');
  const [source, setSource] = useState<Citation | null>(null);
  const [initialTicket, setInitialTicket] = useState<string | null>(null);
  const [examples, setExamples] = useState<Example[]>(fallbackExamples);
  const [waiting, setWaiting] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const closeMenu = useCallback(() => setMenuOpen(false), []);
  const thread = useRef<HTMLDivElement>(null);
  const publishedTickets = new Set(messages.flatMap((message) => message.role === 'quality' && message.ticket_id ? [message.ticket_id] : []));
  const hasPendingTicket = messages.some(({ result }) => result?.ticket && !publishedTickets.has(result.ticket.ticket_id));
  const lastMessage = messages.reduce<Message | undefined>((last, message) => message.role === 'quality' ? last : message, undefined);
  const clarificationPending = lastMessage?.result?.status === 'needs_clarification';

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
        if (controller.signal.aborted) return;
        setSessions(rows);
        const previousId = sessionStorage.getItem(sessionKey);
        const selected = rows.find(({ session_id }) => session_id === previousId);
        if (!selected) return;
        const history = await loadHistory(token, selected.session_id, controller.signal);
        if (controller.signal.aborted) return;
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
    const controller = new AbortController();
    request<Example[]>('/chat/examples', token, undefined, controller.signal)
      .then((rows) => { if (rows.length) setExamples(rows); })
      .catch(() => undefined);
    return () => controller.abort();
  }, [token]);

  // Счётчик на кнопке «Служба качества» / «Очередь»: обращения, которые ещё ждут ответа.
  useEffect(() => {
    const controller = new AbortController();
    request<{ status: string }[]>(isQuality ? '/tickets?status=open' : '/my/tickets', token, undefined, controller.signal)
      .then((rows) => setWaiting(rows.filter(({ status }) => status !== 'published').length))
      .catch(() => undefined);
    return () => controller.abort();
  }, [token, isQuality, screen, messages.length]);

  // Лента сама листается к последнему сообщению; поле ввода закреплено под ней и не двигается.
  useEffect(() => {
    const element = thread.current;
    if (element && screen === 'chat') element.scrollTo({ top: element.scrollHeight, behavior: loadingHistory ? 'auto' : 'smooth' });
  }, [messages, screen, step, loadingHistory]);

  useEffect(() => {
    if (!session || busy || screen !== 'chat' || !hasPendingTicket) return;
    const controller = new AbortController();
    let timer: number;
    const started = Date.now();
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
      // Первую минуту проверяем часто, дальше раз в 15 с: эксперт отвечает не мгновенно.
      timer = window.setTimeout(poll, Date.now() - started < 60_000 ? 3000 : 15_000);
    }
    void poll();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [busy, screen, hasPendingTicket, onExpired, session, token]);

  async function selectChat(id: string) {
    if (busy) return;
    if (id === session) { setScreen('chat'); return; }
    setBusy(true);
    setLoadingHistory(true);
    setError('');
    try {
      const history = await loadHistory(token, id);
      setSession(id);
      sessionStorage.setItem(sessionKey, id);
      setMessages(history.messages);
      setQuestion('');
      setScreen('chat');
    } catch (cause) {
      reportError(cause, 'Не удалось открыть чат.');
      setScreen('chat');
    } finally {
      setBusy(false);
      setLoadingHistory(false);
    }
  }

  // «Задайте вопрос» открывает новый чат. На сервере он создаётся первым вопросом, поэтому пустые чаты не копятся.
  function newChat() {
    if (busy) return;
    setError('');
    setSession('');
    sessionStorage.removeItem(sessionKey);
    setMessages([]);
    setQuestion('');
    setScreen('chat');
  }

  async function send(value: string) {
    const text = value.trim();
    if (!text || busy) return;
    setBusy(true);
    setQuestion('');
    setError('');
    setStep('Отправляю вопрос…');
    const temporaryId = `pending-${Date.now()}`;
    setMessages((items) => [...items, { message_id: temporaryId, role: 'user', content: text }]);
    try {
      let id = session;
      if (!id) {
        const created = await newSession(token);
        id = created.session_id;
        setSession(id);
        sessionStorage.setItem(sessionKey, id);
        setSessions((items) => [{ ...created, created_at: new Date().toISOString(), title: text }, ...items]);
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

  function navigate(next: Screen) {
    setInitialTicket(null);
    setScreen(next);
  }

  function openTicket(ticket: string) {
    setInitialTicket(ticket);
    setScreen(isQuality ? 'quality' : 'requests');
  }

  const retry = error && !session && !busy && !messages.length;

  return (
    <div className="app">
      <Sidebar user={user} screen={screen} waiting={waiting} sessions={sessions} open={menuOpen} onClose={closeMenu}
        activeSession={session} loadingHistory={loadingHistory} busy={busy} signingOut={signingOut}
        onAsk={newChat} onNavigate={navigate} onSelectChat={(id) => void selectChat(id)} onSignOut={onSignOut} />
      <WorkspaceControls isQuality={isQuality} navigationOpen={menuOpen} hasWaiting={waiting > 0} busy={busy}
        onOpenNavigation={() => setMenuOpen(true)} onNewChat={newChat} />
      <main className="main">
        {screen === 'quality' && isQuality ? <QualityDesk token={token} onExpired={onExpired} initialTicket={initialTicket} />
          : screen === 'knowledge' && isQuality ? <KnowledgeBase token={token} onExpired={onExpired} />
          : screen === 'requests' && !isQuality ? <MyRequests token={token} onExpired={onExpired} initialTicket={initialTicket}
            openChat={(id) => void selectChat(id)} />
          : (
            <section className="chat" aria-label="Чат с помощником">
              {error && <p className="alert" role="alert">{error}
                {retry && <> <button type="button" className="link-btn" onClick={() => {
                  setError(''); setBusy(true); setLoadingHistory(true); setLoadAttempt((attempt) => attempt + 1);
                }}>Повторить</button></>}
              </p>}
              {!messages.length ? (
                <div className="empty">
                  <h1>Чем могу помочь?</h1>
                  <div className="ask-card surface">
                    <h2>Примеры запросов</h2>
                    <div className="examples">
                      {examples.map((example) => (
                        <button className="example" key={example.question} type="button" disabled={busy}
                          onClick={() => void send(example.question)}>
                          <strong>{example.title}</strong><span>{example.question}</span>
                          <img src={assets.exampleArrow} width={10.75} height={11.05} alt="" />
                        </button>
                      ))}
                    </div>
                    <Composer value={question} onChange={setQuestion} onSend={() => void send(question)} busy={busy}
                      disabled={loadingHistory} placeholder="Например, кто согласует заявку?" autoFocus />
                  </div>
                  <p className="hint">Задайте вопрос о рабочем процессе. Если ответ есть в базе, вы увидите точный пункт.</p>
                </div>
              ) : <>
                <div ref={thread} className="thread" aria-live="polite">
                  {messages.map((message) => {
                    if (message.role === 'user') return <p key={message.message_id} className="msg-user">{message.content}</p>;
                    if (message.role === 'quality') return <QualityReply key={message.message_id} content={message.content} ticketId={message.ticket_id} />;
                    return message.result ? (
                      <AnswerCard key={message.message_id} result={message.result} busy={busy}
                        clarificationActive={message.message_id === lastMessage?.message_id}
                        ask={(value) => void send(value)} openSource={setSource} openTicket={openTicket}
                        ticketPublished={publishedTickets.has(message.result.ticket?.ticket_id ?? '')} />
                    ) : <article key={message.message_id} className="answer surface"><p className="answer-text">{message.content}</p></article>;
                  })}
                  {step && <p className="working surface" role="status"><span className="pulse" aria-hidden="true" />{step}</p>}
                </div>
                <div className="dock">
                  <Composer value={question} onChange={setQuestion} onSend={() => void send(question)} busy={busy}
                    disabled={loadingHistory} placeholder={clarificationPending ? 'Или напишите своими словами…' : 'Спросите ещё…'} />
                </div>
              </>}
            </section>
          )}
      </main>
      {source && <SourceDialog source={source} close={() => setSource(null)} />}
    </div>
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

  if (restoring) return <div className="signin-page"><p className="signin surface" role="status">Проверяю вход…</p></div>;
  if (!auth) {
    return <SignIn notice={notice} onSignedIn={(next) => {
      sessionStorage.setItem(tokenKey, next.token);
      setAuth(next);
      setNotice('');
    }} />;
  }
  return <Workspace key={auth.token} auth={auth} onExpired={expire} onSignOut={() => void signOut()} signingOut={signingOut} />;
}

createRoot(document.getElementById('root')!).render(<App />);
