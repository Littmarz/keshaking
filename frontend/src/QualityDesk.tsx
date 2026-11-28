import { useCallback, useEffect, useState } from 'react';
import { EnvelopeSimpleIcon } from '@phosphor-icons/react';
import { ApiError, request, type Citation, type Entry, type Ticket } from './api';
import { Tag } from './Answer';
import { Icon } from './brand';
import { ticketDate } from './dates';

type QueueTicket = Ticket & {
  user_id?: string;
  context?: {
    reason?: string;
    conflict_sources?: Pick<Citation, 'chunk_id' | 'doc_title' | 'section' | 'source_type'>[];
  };
};
type TicketDetail = QueueTicket & { qa_entry: Entry | null; test_mail?: boolean };
type Filter = 'all' | 'open' | 'closed';

const STATUSES: Ticket['status'][] = ['open', 'answered', 'published'];

function statusTag(ticket: QueueTicket) {
  if (ticket.status === 'published') return <Tag tone="ok">Ответ дан</Tag>;
  if (ticket.status === 'answered') return <Tag tone="info">Черновик</Tag>;
  if (ticket.context?.conflict_sources?.length) return <Tag tone="bad">Расхождение</Tag>;
  return <Tag tone="wait">Ждёт ответа</Tag>;
}

export function QualityDesk({ initialTicket, token, onExpired }: {
  initialTicket: string | null;
  token: string;
  onExpired: (token: string) => void;
}) {
  const [tickets, setTickets] = useState<QueueTicket[]>([]);
  const [selected, setSelected] = useState<TicketDetail | null>(null);
  const [answer, setAnswer] = useState('');
  const [docId, setDocId] = useState('');
  const [section, setSection] = useState('');
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
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
        const groups = await Promise.all(STATUSES.map((status) => (
          request<QueueTicket[]>(`/tickets?status=${status}`, token, undefined, controller.signal)
        )));
        const rows = groups.flat().sort((a, b) => b.created_at.localeCompare(a.created_at));
        const first = rows.find((row) => row.ticket_id === initialTicket) ?? rows.find((row) => row.status !== 'published') ?? rows[0];
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
    if (busy || id === selected?.ticket_id) return;
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

  const saved = selected?.qa_entry;
  const hasEdits = saved?.answer !== answer.trim() || (saved?.doc_id ?? '') !== docId.trim() || (saved?.section ?? '') !== section.trim();

  async function saveDraft(): Promise<Entry | null> {
    if (!selected) return null;
    const entry = await request<Entry>(`/tickets/${selected.ticket_id}/answer`, token, {
      answer: answer.trim(), doc_id: docId.trim() || null, section: section.trim() || null,
    });
    updateTicket(entry, 'answered');
    return entry;
  }

  async function run(action: () => Promise<void>, fallback: string) {
    if (busy) return;
    setError('');
    setBusy(true);
    try {
      await action();
    } catch (cause) {
      reportError(cause, fallback);
    } finally {
      setBusy(false);
    }
  }

  // «Опубликовать в базу»: если черновика нет или он изменён — сначала сохраняем, затем подтверждаем.
  const publish = () => run(async () => {
    const entry = !saved || hasEdits ? await saveDraft() : saved;
    if (!entry) return;
    const confirmed = await request<Entry>(`/qa/${entry.qa_entry_id}/confirm`, token, {});
    updateTicket(confirmed, 'published');
  }, 'Публикация не удалась.');

  const draft = () => run(async () => { await saveDraft(); }, 'Черновик не сохранился.');

  const testMail = () => run(async () => {
    if (!selected) return;
    const entry = await request<Entry>(`/tickets/${selected.ticket_id}/test-mail-reply`, token, { answer: answer.trim() });
    setAnswer(entry.answer);
    updateTicket(entry, 'answered');
  }, 'Тестовый ответ не загрузился.');

  const open = tickets.filter(({ status }) => status !== 'published');
  const closed = tickets.filter(({ status }) => status === 'published');
  const shown = filter === 'open' ? open : filter === 'closed' ? closed : tickets;
  const author = selected?.user_id?.startsWith('employee') ? 'сотрудник' : selected?.user_id;

  return (
    <div className="quality-layout">
      <section className="page" aria-labelledby="quality-heading">
        <h1 id="quality-heading">Обращения</h1>
        <p className="lead">Вопросы, на которые не нашлось ответа в регламентах.</p>
        <div className="filters" role="group" aria-label="Фильтр обращений">
          <button type="button" className="pill" aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>Все · {tickets.length}</button>
          <button type="button" className="pill" aria-pressed={filter === 'open'} onClick={() => setFilter('open')}>Открытые · {open.length}</button>
          <button type="button" className="pill" aria-pressed={filter === 'closed'} onClick={() => setFilter('closed')}>Закрытые · {closed.length}</button>
        </div>
        {error && <p className="alert" role="alert">{error}{' '}
          <button type="button" className="link-btn" onClick={() => setLoadAttempt((attempt) => attempt + 1)}>Обновить</button></p>}
        {busy && !tickets.length && <p className="empty-note" role="status">Загружаю очередь…</p>}
        {!busy && !shown.length && !error && <p className="empty-note">Здесь появятся вопросы без ответа и сообщения о расхождениях.</p>}
        <div className="cards">
          {shown.map((ticket) => (
            <button key={ticket.ticket_id} type="button" className="ticket surface" disabled={busy && ticket.ticket_id !== selected?.ticket_id}
              aria-current={selected?.ticket_id === ticket.ticket_id ? 'true' : undefined} onClick={() => void openTicket(ticket.ticket_id)}>
              <span className="ticket-head">
                <span className="ticket-id">{ticket.ticket_id}</span>
                {statusTag(ticket)}
                <time className="ticket-date" dateTime={ticket.created_at}>{ticketDate(ticket.created_at)}</time>
              </span>
              <span className="ticket-q">{ticket.question}</span>
            </button>
          ))}
        </div>
      </section>

      {selected && (
        <aside className="review" aria-label={`Обращение ${selected.ticket_id}`}>
          <div className="review-panel surface">
            <p className="review-meta">{selected.ticket_id} · {author}{selected.test_mail ? ' · письмо в службу качества' : ''}</p>
            <h2>{selected.question}</h2>
            {selected.context?.reason && <div className="review-reason">
              {selected.context.reason}
              {selected.context.conflict_sources?.length ? <ul>
                {selected.context.conflict_sources.map((source) => (
                  <li key={source.chunk_id}>{source.doc_title}{source.source_type === 'regulation' && ` · п. ${source.section}`}</li>
                ))}
              </ul> : null}
            </div>}
            {selected.status === 'published' ? (
              <div className="published-box" role="status">
                <strong><Icon name="checks" size={18} />Опубликовано в базе знаний</strong>
                {saved?.answer}
              </div>
            ) : <>
              <label className="sr-only" htmlFor="review-answer">Ответ эксперта</label>
              <textarea id="review-answer" value={answer} onChange={(event) => setAnswer(event.target.value)} maxLength={8000}
                placeholder="Напишите разъяснение, которое увидит сотрудник" />
              <div className="review-fields">
                <label><span>Регламент</span>
                  <input value={docId} onChange={(event) => setDocId(event.target.value)} placeholder="например, ib-presale" /></label>
                <label><span>Пункт</span>
                  <input value={section} onChange={(event) => setSection(event.target.value)} placeholder="например, 15" /></label>
              </div>
            </>}
          </div>
          {selected.status !== 'published' && <>
            <div className="review-actions">
              <button type="button" className="btn primary" disabled={busy || !answer.trim()} onClick={() => void publish()}>
                <Icon name="checks" size={30} />Опубликовать в базу
              </button>
              <button type="button" className="btn" disabled={busy || !answer.trim() || !hasEdits} onClick={() => void draft()}>
                Сохранить как черновик
              </button>
              {selected.status === 'open' && selected.test_mail && (
                <button type="button" className="btn wide" disabled={busy || !answer.trim()} onClick={() => void testMail()}>
                  <EnvelopeSimpleIcon size={20} aria-hidden="true" />Ответить тестовым письмом
                </button>
              )}
            </div>
            <p className="review-note">После публикации сотрудник получит ответ в чате и на почту, а агент начнёт использовать его в ответах.</p>
          </>}
        </aside>
      )}
    </div>
  );
}
