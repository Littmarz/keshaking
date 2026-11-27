import { useEffect, useState, type FormEvent } from 'react';
import { login, request, type Entry, type Ticket } from './api';

const statuses = { open: 'Открыто', answered: 'Черновик', published: 'Опубликовано' };
type TicketDetail = Ticket & { qa_entry: Entry | null; test_mail?: boolean };

export function QualityDesk({ initialTicket }: { initialTicket: string | null }) {
  const [token, setToken] = useState('');
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [selected, setSelected] = useState<TicketDetail | null>(null);
  const [answer, setAnswer] = useState('');
  const [docId, setDocId] = useState('');
  const [section, setSection] = useState('');
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');

  function showTicket(ticket: TicketDetail) {
    setSelected(ticket);
    setAnswer(ticket.qa_entry?.answer ?? '');
    setDocId(ticket.qa_entry?.doc_id ?? '');
    setSection(ticket.qa_entry?.section ?? '');
  }

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const key = await login('quality');
        const groups = await Promise.all(Object.keys(statuses).map((status) => request<Ticket[]>(`/tickets?status=${status}`, key)));
        const rows = groups.flat().sort((a, b) => b.created_at.localeCompare(a.created_at));
        const first = rows.find((row) => row.ticket_id === initialTicket) ?? rows[0];
        const detail = first ? await request<TicketDetail>(`/tickets/${first.ticket_id}`, key) : null;
        if (!active) return;
        setToken(key);
        setTickets(rows);
        if (detail) showTicket(detail);
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : 'Очередь недоступна.');
      } finally {
        if (active) setBusy(false);
      }
    }
    void load();
    return () => { active = false; };
  }, [initialTicket]);

  async function openTicket(id: string) {
    setBusy(true);
    setError('');
    try {
      showTicket(await request<TicketDetail>(`/tickets/${id}`, token));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Обращение недоступно.');
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
      setError(cause instanceof Error ? cause.message : 'Черновик не сохранился.');
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!selected?.qa_entry || busy) return;
    setError('');
    setBusy(true);
    try {
      const path = selected.test_mail
        ? `/tickets/${selected.ticket_id}/test-mail-reply`
        : `/qa/${selected.qa_entry.qa_entry_id}/confirm`;
      const entry = await request<Entry>(path, token, {});
      updateTicket(entry, 'published');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Публикация не удалась.');
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
          <h2>Обращения <span className="counter">{tickets.length}</span></h2>
          {busy && !tickets.length && <p role="status">Загружаю очередь…</p>}
          {!busy && !tickets.length && !error && <p>Здесь появятся вопросы, на которые агент не нашёл ответа.</p>}
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
            {selected.status === 'published' ? (
              <div className="published" role="status">
                <strong>{saved?.confirmed_by === 'quality-mail' ? 'Ответ из письма опубликован' : 'Ответ опубликован'}</strong>
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
                  {saved && (
                    <button className="primary" type="button" disabled={busy || hasEdits} onClick={() => void confirm()}>
                      {selected.test_mail ? 'Отправить тестовый ответ письмом' : 'Подтвердить и опубликовать'}
                    </button>
                  )}
                </div>
                {saved && <p className="draft-note" role="status">
                  {selected.test_mail
                    ? 'Черновик сохранён. После отправки «Пункт» опубликует полученный из почты ответ.'
                    : 'Черновик сохранён. После подтверждения его можно найти из нового чата.'}
                </p>}
              </form>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
