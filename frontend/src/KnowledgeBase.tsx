import { useCallback, useEffect, useState } from 'react';
import { ApiError, request } from './api';
import { Tag } from './Answer';
import { ticketDate } from './dates';

type KnowledgeEntry = {
  qa_entry_id: string;
  ticket_id: string;
  question: string;
  answer: string;
  doc_id: string | null;
  section: string | null;
  confirmed_at: string | null;
};

// Опубликованные разъяснения службы качества — то, что агент использует в ответах наравне с регламентами.
export function KnowledgeBase({ token, onExpired }: { token: string; onExpired: (token: string) => void }) {
  const [entries, setEntries] = useState<KnowledgeEntry[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');

  const reportError = useCallback((cause: unknown) => {
    if (cause instanceof ApiError && cause.status === 401) onExpired(token);
    else setError(cause instanceof Error ? cause.message : 'База знаний недоступна.');
  }, [onExpired, token]);

  useEffect(() => {
    const controller = new AbortController();
    request<KnowledgeEntry[]>('/qa', token, undefined, controller.signal)
      .then((rows) => { if (!controller.signal.aborted) setEntries(rows); })
      .catch((cause) => { if (!controller.signal.aborted) reportError(cause); })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [token, reportError]);

  return (
    <section className="page" aria-labelledby="kb-heading">
      <h1 id="kb-heading">База знаний</h1>
      <p className="lead">Подтверждённые разъяснения службы качества. Агент опирается на них, если в регламентах нет правила.</p>
      {error && <p className="alert" role="alert">{error}</p>}
      {busy && <p className="empty-note" role="status">Загружаю разъяснения…</p>}
      {!busy && !entries.length && !error && <p className="empty-note">Пока пусто. Опубликуйте ответ на обращение, и он появится здесь.</p>}
      <div className="cards">
        {entries.map((entry) => (
          <article key={entry.qa_entry_id} className="ticket surface">
            <div className="ticket-head">
              <span className="ticket-id">{entry.ticket_id}</span>
              <Tag tone="ok">Опубликовано</Tag>
              {entry.confirmed_at && <time className="ticket-date" dateTime={entry.confirmed_at}>{ticketDate(entry.confirmed_at)}</time>}
            </div>
            <p className="ticket-q">{entry.question}</p>
            <div className="reply-box">{entry.answer}</div>
            {(entry.doc_id || entry.section) && <p className="kb-source">
              Опора: {[entry.doc_id, entry.section && `п. ${entry.section}`].filter(Boolean).join(' · ')}
            </p>}
          </article>
        ))}
      </div>
    </section>
  );
}
