import { useCallback, useEffect, useState } from 'react';
import { ApiError, request, type MyTicket } from './api';

const statuses: Record<MyTicket['status'], string> = {
  open: 'Ждёт ответа', answered: 'Ждёт ответа', published: 'Ответ получен',
};
const ticketDate = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

export function MyRequests({ token, onExpired, initialTicket, openChat }: {
  token: string;
  onExpired: (token: string) => void;
  initialTicket: string | null;
  openChat: (sessionId: string) => void;
}) {
  const [tickets, setTickets] = useState<MyTicket[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [loadAttempt, setLoadAttempt] = useState(0);

  const reportError = useCallback((cause: unknown) => {
    if (cause instanceof ApiError && cause.status === 401) onExpired(token);
    else setError(cause instanceof Error ? cause.message : 'Обращения недоступны.');
  }, [onExpired, token]);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setBusy(true);
      setError('');
      try {
        const rows = await request<MyTicket[]>('/my/tickets', token, undefined, controller.signal);
        if (!controller.signal.aborted) setTickets(rows);
      } catch (cause) {
        if (!controller.signal.aborted) reportError(cause);
      } finally {
        if (!controller.signal.aborted) setBusy(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [token, loadAttempt, reportError]);

  useEffect(() => {
    if (initialTicket && tickets.length) document.getElementById(`ticket-${initialTicket}`)?.scrollIntoView({ block: 'center' });
  }, [initialTicket, tickets]);

  const waiting = tickets.filter(({ status }) => status !== 'published').length;

  return (
    <section className="quality" aria-labelledby="requests-heading">
      <div className="workspace-heading">
        <h1 id="requests-heading">Мои обращения</h1>
        <p>Вопросы, на которые не нашлось ответа в регламентах. Они ушли письмом в службу качества, вы в копии.</p>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="card queue">
        <div className="queue-title">
          <h2>Обращения <span className="counter">{tickets.length}</span>
            {waiting > 0 && <span className="requests-waiting">ждут ответа: {waiting}</span>}</h2>
          <button className="secondary" type="button" disabled={busy} onClick={() => setLoadAttempt((attempt) => attempt + 1)}>
            Обновить
          </button>
        </div>
        {busy && !tickets.length && <p role="status">Загружаю обращения…</p>}
        {!busy && !tickets.length && !error && <p>Пока обращений нет. Если агент не найдёт ответ, вопрос появится здесь.</p>}
        <ul className="requests">
          {tickets.map((ticket) => (
            <li key={ticket.ticket_id} id={`ticket-${ticket.ticket_id}`}
              className={`request request-${ticket.status === 'published' ? 'done' : 'waiting'}`}
              aria-current={ticket.ticket_id === initialTicket ? 'true' : undefined}>
              <div className="ticket-meta">
                <strong>{ticket.ticket_id}</strong>
                <span className="request-status">{statuses[ticket.status]}</span>
                <time dateTime={ticket.created_at}>{ticketDate.format(new Date(ticket.created_at))}</time>
              </div>
              <p className="request-question">{ticket.question}</p>
              {ticket.answer && <div className="published">
                <strong>Ответ службы качества</strong>
                <p>{ticket.answer}</p>
                {(ticket.doc_id || ticket.section) && <small>
                  {[ticket.doc_id, ticket.section && `п. ${ticket.section}`].filter(Boolean).join(' · ')}
                </small>}
              </div>}
              <button type="button" className="secondary" onClick={() => openChat(ticket.session_id)}>Открыть чат</button>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
