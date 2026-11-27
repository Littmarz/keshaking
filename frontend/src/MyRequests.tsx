import { useCallback, useEffect, useState } from 'react';
import { ChatTextIcon } from '@phosphor-icons/react';
import { ApiError, request, type MyTicket } from './api';
import { Tag } from './Answer';
import { ticketDate } from './dates';

type Filter = 'all' | 'waiting' | 'answered';

export function MyRequests({ token, onExpired, initialTicket, openChat }: {
  token: string;
  onExpired: (token: string) => void;
  initialTicket: string | null;
  openChat: (sessionId: string) => void;
}) {
  const [tickets, setTickets] = useState<MyTicket[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState<Filter>('all');

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
  }, [token, reportError]);

  useEffect(() => {
    if (initialTicket && tickets.length) document.getElementById(`ticket-${initialTicket}`)?.scrollIntoView({ block: 'center' });
  }, [initialTicket, tickets]);

  const waiting = tickets.filter(({ status }) => status !== 'published');
  const answered = tickets.filter(({ status }) => status === 'published');
  const shown = filter === 'waiting' ? waiting : filter === 'answered' ? answered : tickets;

  return (
    <section className="page" aria-labelledby="requests-heading">
      <h1 id="requests-heading">Мои обращения</h1>
      <p className="lead">Вопросы, на которые не нашлось ответа в регламентах. Вы получаете письмо по каждому.</p>
      <div className="filters" role="group" aria-label="Фильтр обращений">
        <button type="button" className="pill" aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>Все · {tickets.length}</button>
        <button type="button" className="pill" aria-pressed={filter === 'waiting'} onClick={() => setFilter('waiting')}>Ждут ответа · {waiting.length}</button>
        <button type="button" className="pill" aria-pressed={filter === 'answered'} onClick={() => setFilter('answered')}>С ответом · {answered.length}</button>
      </div>
      {error && <p className="alert" role="alert">{error}</p>}
      {busy && !tickets.length && <p className="empty-note" role="status">Загружаю обращения…</p>}
      {!busy && !shown.length && !error && <p className="empty-note">
        {tickets.length ? 'В этом списке пока пусто.' : 'Пока обращений нет. Если в регламентах не найдётся ответа, вопрос появится здесь.'}
      </p>}
      <div className="cards">
        {shown.map((ticket) => (
          <article key={ticket.ticket_id} id={`ticket-${ticket.ticket_id}`} className="ticket surface"
            aria-current={ticket.ticket_id === initialTicket ? 'true' : undefined}>
            <div className="ticket-head">
              <span className="ticket-id">{ticket.ticket_id}</span>
              {ticket.status === 'published' ? <Tag tone="ok">Ответ получен</Tag> : <Tag tone="wait">Ждёт ответа</Tag>}
              <time className="ticket-date" dateTime={ticket.created_at}>{ticketDate(ticket.created_at)}</time>
            </div>
            <p className="ticket-q">{ticket.question}</p>
            {ticket.answer && <div className="reply-box">
              {ticket.answer}
              {(ticket.doc_id || ticket.section) && <small>{[ticket.doc_id, ticket.section && `п. ${ticket.section}`].filter(Boolean).join(' · ')}</small>}
            </div>}
            <button type="button" className="link-btn" onClick={() => openChat(ticket.session_id)}>
              <ChatTextIcon size={24} aria-hidden="true" />Открыть чат
            </button>
          </article>
        ))}
      </div>
    </section>
  );
}
