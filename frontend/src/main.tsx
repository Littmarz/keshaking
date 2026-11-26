import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { createRoot } from 'react-dom/client';
import { askQuestion, login, request, type Answer, type Citation, type Message, type Session } from './api';
import { QualityDesk } from './QualityDesk';
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

function AnswerCard({ result, busy, clarificationActive, ask, openSource, openQueue }: {
  result: Answer; busy: boolean; clarificationActive: boolean; ask: (question: string) => void;
  openSource: (source: Citation) => void; openQueue: (ticket: string) => void;
}) {
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
      {result.ticket && <button type="button" className="secondary" disabled={busy}
        onClick={() => openQueue(result.ticket!.ticket_id)}>Открыть обращение {result.ticket.ticket_id}</button>}
    </article>
  );
}

function App() {
  const [screen, setScreen] = useState<'chat' | 'quality'>('chat');
  const [token, setToken] = useState('');
  const [session, setSession] = useState('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(true);
  const [step, setStep] = useState('');
  const [error, setError] = useState('');
  const [source, setSource] = useState<Citation | null>(null);
  const [initialTicket, setInitialTicket] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let active = true;
    async function initialize() {
      try {
        const key = await login('employee');
        const sessions = await request<Session[]>('/chat/sessions', key);
        const selected = sessions[0] ?? await request<Session>('/chat/sessions', key, {});
        const history = await request<{ messages: Message[] }>(`/chat/sessions/${selected.session_id}`, key);
        if (!active) return;
        setToken(key);
        setSession(selected.session_id);
        setMessages(history.messages);
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : 'Бэкенд недоступен.');
      } finally {
        if (active) setBusy(false);
      }
    }
    void initialize();
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (messages.length && screen === 'chat') end.current?.scrollIntoView({ block: 'nearest' });
  }, [messages, screen]);

  async function newChat() {
    if (busy || !token) return;
    setBusy(true);
    setError('');
    try {
      const created = await request<Session>('/chat/sessions', token, {});
      setSession(created.session_id);
      setMessages([]);
      setQuestion('');
      setScreen('chat');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Чат не создался.');
    } finally {
      setBusy(false);
    }
  }

  async function send(value: string) {
    const text = value.trim();
    if (!text || busy || !session || !token) return;
    setBusy(true);
    setQuestion('');
    setError('');
    setStep('Получаю ответ…');
    const temporaryId = `pending-${Date.now()}`;
    setMessages((items) => [...items, { message_id: temporaryId, role: 'user', content: text }]);
    try {
      const result = await askQuestion(token, session, text, setStep);
      setMessages((items) => [...items, { message_id: result.message_id, role: 'assistant', content: result.answer, result }]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось получить ответ.');
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
    <div className="site">
      <header className="topbar">
        <div className="brand"><span className="brand-mark" aria-hidden="true"><i /><i /><i /></span><strong>Пункт</strong></div>
        <div className="environment"><span className="live-dot" />Демо · учебная база</div>
      </header>
      <div className="workspace">
        <aside className="sidebar">
          <nav aria-label="Разделы">
            <button type="button" className="nav-link" aria-current={screen === 'chat' ? 'page' : undefined}
              onClick={() => setScreen('chat')}><span className="nav-icon chat-icon" aria-hidden="true" />Задать вопрос</button>
            <button type="button" className="nav-link" aria-current={screen === 'quality' ? 'page' : undefined}
              onClick={() => showQueue()}><span className="nav-icon queue-icon" aria-hidden="true" />Служба качества</button>
          </nav>
          <button className="new-chat" type="button" disabled={!token || busy} onClick={() => void newChat()}><span aria-hidden="true">+</span> Новый чат</button>
        </aside>
        <main className="main">
          {screen === 'quality' ? <QualityDesk initialTicket={initialTicket} /> : (
            <section className="chat" aria-labelledby="chat-heading">
              <div className="hero">
                <h1 id="chat-heading">Ответы по регламентам</h1>
                <p>Задайте вопрос о рабочем процессе. Если ответ есть в базе, вы увидите точный пункт.</p>
                <div className="hero-shape" aria-hidden="true"><span /><span /><span /></div>
              </div>
              <div className="chat-card card">
                {error && <p className="error" role="alert">{error}{!token && ' Проверьте, что FastAPI запущен на порту 8000, и обновите страницу.'}</p>}
                {!messages.length && <div className="examples">
                  <h2>Примеры вопросов</h2>
                  <div className="example-grid">
                    {examples.map((example) => <button className="example" key={example.question} type="button"
                      disabled={busy || !token} onClick={() => void send(example.question)}>
                      <strong>{example.title}</strong><span>{example.question}</span><span className="example-arrow arrow-up-right" aria-hidden="true" />
                    </button>)}
                  </div>
                </div>}
                {messages.length > 0 && <div className="thread" aria-live="polite">
                  {messages.map((message, index) => message.role === 'user' ? (
                    <p key={message.message_id} className="user-message">{message.content}</p>
                  ) : message.result ? (
                    <AnswerCard key={message.message_id} result={message.result} busy={busy}
                      clarificationActive={index === messages.length - 1}
                      ask={(value) => void send(value)} openSource={setSource} openQueue={showQueue} />
                  ) : <p key={message.message_id}>{message.content}</p>)}
                  <div ref={end} />
                </div>}
                {step && <p className="working" role="status"><span className="live-dot" />{step}</p>}
                <form className="composer" onSubmit={submit}>
                  <textarea aria-label="Ваш вопрос" value={question} onChange={(e) => setQuestion(e.target.value)} onKeyDown={shortcut}
                    placeholder="Например, кто согласует заявку?" rows={2} maxLength={8000} disabled={!token} />
                  <button className="primary send" disabled={!question.trim() || busy || !token}>Спросить <span aria-hidden="true">↑</span></button>
                </form>
              </div>
            </section>
          )}
        </main>
      </div>
      <footer className="build-footer">
        {import.meta.env.VITE_PUNKT_BUILD_VERSION
          ? `Сборка ${import.meta.env.VITE_PUNKT_BUILD_VERSION}`
          : 'Сборка без номера'}
      </footer>
      {source && <SourceDialog source={source} close={() => setSource(null)} />}
    </div>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
