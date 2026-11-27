import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { ApiError, request, type Citation, type Entry, type Ticket } from './api';

const statuses = { open: 'Открыто', answered: 'Черновик', published: 'Опубликовано' };
type TicketDetail = Ticket & {
  qa_entry: Entry | null;
  test_mail?: boolean;
  context?: {
    reason?: string;
    conflict_sources?: Pick<Citation, 'chunk_id' | 'doc_title' | 'section' | 'source_type'>[];
  };
};

export function QualityDesk({ initialTicket, token, onExpired }: {
  initialTicket: string | null;
  token: string;
  onExpired: (token: string) => void;
}) {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [selected, setSelected] = useState<TicketDetail | null>(null);
  const [answer, setAnswer] = useState('');
  const [docId, setDocId] = useState('');
  const [section, setSection] = useState('');
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [loadAttempt, setLoadAttempt] = useState(0);

  function showTicket(ticket: TicketDetail | null) {
    setSelected(ticket);
    setAnswer(ticket?.qa_entry?.answer ?? '');
    setDocId(ticket?.qa_entry?.doc_id ?? '');
    setSection(ticket?.qa_entry?.section ?? '');
  }

  const reportError = useCallback((cause: unknown, fallback: string) => {
    if (cause instanceof ApiError && cause.status === 401) {
      onExpired(token);
    } else {
      setError(cause instanceof Error ? cause.message : fallback);
    }
  }, [onExpired, token]);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setBusy(true);
      setError('');
      try {
        const groups = await Promise.all(Object.keys(statuses).map((status) => (
          request<Ticket[]>(`/tickets?status=${status}`, token, undefined, controller.signal)
        )));
        const rows = groups.flat().sort((a, b) => b.created_at.localeCompare(a.created_at));
        const first = rows.find((row) => row.ticket_id === initialTicket) ?? rows[0];
        const detail = first ? await request<TicketDetail>(`/tickets/${first.ticket_id}`, token, undefined, controller.signal) : null;
        if (controller.signal.aborted) return;
        setTickets(rows);
        showTicket(detail);
      } catch (cause) {
        if (!controller.signal.aborted) reportError(cause, 'Очередь недоступна.');
      } finally {
        if (!controller.signal.aborted) setBusy(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [initialTicket, token, loadAttempt, reportError]);

  async function openTicket(id: string) {
    setBusy(true);
    setError('');
    try {
      showTicket(await request<TicketDetail>(`/tickets/${id}`, token));
    } catch (cause) {
      reportError(cause, 'Обращение недоступно.');
    } finally {
      setBusy(false);
    }
  }

  function updateTicket(entry: Entry, status: Ticket['status']) {
    setSelected((current) => current ? { ...current, status, qa_entry: entry } : null);
    setTickets((rows) => rows.map((row) => row.ticket_id === selected?.ticket_id ? { ...row, status } : row));
  }

  async function saveDraft(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected || busy || !answer.trim()) return;
    setError('');
    setBusy(true);
    try {
      const entry = await request<Entry>(`/tickets/${selected.ticket_id}/answer`, token, {
        answer: answer.trim(), doc_id: docId.trim() || null, section: section.trim() || null,
      });
      updateTicket(entry, 'answered');
    } catch (cause) {
      reportError(cause, 'Черновик не сохранился.');
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!selected?.qa_entry || busy) return;
    setError('');
    setBusy(true);
    try {
      const entry = await request<Entry>(`/qa/${selected.qa_entry.qa_entry_id}/confirm`, token, {});
      updateTicket(entry, 'published');
    } catch (cause) {
      reportError(cause, 'Публикация не удалась.');
    } finally {
      setBusy(false);
    }
  }

  async function sendTestMail() {
    if (!selected || busy || !answer.trim()) return;
    setError('');
    setBusy(true);
    try {
      const entry = await request<Entry>(`/tickets/${selected.ticket_id}/test-mail-reply`, token, { answer: answer.trim() });
      setAnswer(entry.answer);
      updateTicket(entry, 'answered');
    } catch (cause) {
      reportError(cause, 'Тестовый ответ не загрузился.');
    } finally {
      setBusy(false);
    }
  }

  const saved = selected?.qa_entry;
  const hasEdits = saved?.answer !== answer.trim() || (saved?.doc_id ?? '') !== docId.trim() || (saved?.section ?? '') !== section.trim();

  return (
    <section className="quality" aria-labelledby="quality-heading">
      <div className="workspace-heading">
        <h1 id="quality-heading">Служба качества</h1>
        <p>Проверьте вопрос сотрудника и добавьте подтверждённый ответ в базу знаний.</p>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="quality-grid">
        <div className="card queue">
          <div className="queue-title">
            <h2>Обращения <span className="counter">{tickets.length}</span></h2>
            <button className="secondary" type="button" disabled={busy} onClick={() => setLoadAttempt((attempt) => attempt + 1)}>
              Обновить
            </button>
          </div>
          {busy && !tickets.length && <p role="status">Загружаю очередь…</p>}
          {!busy && !tickets.length && !error && <p>Здесь появятся вопросы без ответа и сообщения о расхождениях.</p>}
          {tickets.map((ticket) => (
            <button className="ticket-row" type="button" key={ticket.ticket_id} disabled={busy}
              aria-pressed={selected?.ticket_id === ticket.ticket_id} onClick={() => void openTicket(ticket.ticket_id)}>
              <span className="ticket-meta"><strong>{ticket.ticket_id}</strong><span>{statuses[ticket.status]}</span></span>
              <span>{ticket.question}</span>
            </button>
          ))}
        </div>
        {selected && (
          <div className="card review">
            <p className="eyebrow">Обращение {selected.ticket_id}</p>
            <h2>{selected.question}</h2>
            {selected.context?.conflict_sources?.length ? (
              <div>
                <p>{selected.context.reason}</p>
                <ul>
                  {selected.context.conflict_sources.map((source) => (
                    <li key={source.chunk_id}>{source.doc_title}
                      {source.source_type === 'regulation' && ` · п. ${source.section}`}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            {selected.status === 'published' ? (
              <div className="published" role="status">
                <strong>Ответ опубликован</strong>
                <p>{saved?.answer}</p>
                <small>Учебное разъяснение службы качества</small>
              </div>
            ) : (
              <form onSubmit={saveDraft} className="review-form">
                <label>Учебный ответ<textarea value={answer} onChange={(e) => setAnswer(e.target.value)}
                  rows={5} required maxLength={8000} placeholder="Напишите разъяснение, которое увидит сотрудник" /></label>
                <div className="review-fields">
                  <label>ID регламента <span>при наличии</span><input value={docId} onChange={(e) => setDocId(e.target.value)} /></label>
                  <label>Пункт <span>при наличии</span><input value={section} onChange={(e) => setSection(e.target.value)} /></label>
                </div>
                <div className="review-actions">
                  <button className="secondary" disabled={busy || !answer.trim() || !hasEdits}>Сохранить черновик</button>
                  {saved && <button className="primary" type="button" disabled={busy || hasEdits} onClick={() => void confirm()}>
                    Подтвердить и опубликовать
                  </button>}
                  {selected.status === 'open' && selected.test_mail && (
                    <button className="secondary" type="button" disabled={busy || !answer.trim()}
                      onClick={() => void sendTestMail()}>Получить черновик из тестового письма</button>
                  )}
                </div>
                {saved && <p className="draft-note" role="status">
                  После подтверждения сотрудник увидит ответ в исходном чате.
                </p>}
              </form>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
