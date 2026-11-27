import { useEffect, useRef, type ReactNode } from 'react';
import { BookOpenTextIcon, CheckCircleIcon, ClockIcon, ProhibitIcon, QuestionIcon, WarningCircleIcon, XIcon } from '@phosphor-icons/react';
import type { Answer, Citation } from './api';
import { assets } from './brand';

type Tone = 'ok' | 'wait' | 'info' | 'bad' | 'plain';

export function Tag({ tone, icon, children }: { tone: Tone; icon?: ReactNode; children: ReactNode }) {
  return <span className={`tag ${tone}`}>{icon}{children}</span>;
}

const questionIcon = <QuestionIcon size={19} aria-hidden="true" />;

function statusTag(result: Answer) {
  const fromQuality = result.citations.length > 0 && result.citations.every((source) => source.source_type === 'qa_entry');
  switch (result.status) {
    case 'answered':
      return <Tag tone="ok" icon={<img src={assets.checks} width={22} height={22} alt="" />}>
        {fromQuality ? 'Ответ службы качества' : 'Ответ по регламенту'}
      </Tag>;
    case 'partial':
      return <Tag tone="wait" icon={questionIcon}>Частичный ответ</Tag>;
    case 'needs_clarification':
      return <Tag tone="wait" icon={questionIcon}>Нужно уточнение</Tag>;
    case 'escalated':
      return <Tag tone="info" icon={questionIcon}>Передано в службу качества</Tag>;
    case 'conflict':
      return <Tag tone="bad" icon={<WarningCircleIcon size={19} aria-hidden="true" />}>Источники расходятся</Tag>;
    default:
      return <Tag tone="plain" icon={<ProhibitIcon size={19} aria-hidden="true" />}>Вне базы регламентов</Tag>;
  }
}

function sourceLabel(source: Citation) {
  return source.source_type === 'qa_entry' ? source.doc_title : `${source.doc_title} · п. ${source.section}`;
}

export function AnswerCard({ result, busy, clarificationActive, ask, openSource, openTicket, ticketPublished }: {
  result: Answer;
  busy: boolean;
  clarificationActive: boolean;
  ask: (question: string) => void;
  openSource: (source: Citation) => void;
  openTicket: (ticket: string) => void;
  ticketPublished: boolean;
}) {
  const ticketId = result.ticket?.ticket_id;
  const options = clarificationActive ? result.clarification?.options ?? [] : [];
  return (
    <article className="answer surface" aria-label="Ответ помощника">
      {statusTag(result)}
      <p className="answer-text">
        {result.answer.split(/\[(\d+)\]/g).map((part, index) => {
          if (index % 2 === 0) return part.replace(/Q-(\d+)/g, 'Q‑$1');  // номер обращения без переноса по дефису
          const source = result.citations[Number(part) - 1];
          return source ? <button className="cite" type="button" key={index} onClick={() => openSource(source)}
            aria-label={`Источник ${part}: ${sourceLabel(source)}`}>{part}</button> : `[${part}]`;
        })}
      </p>
      {result.citations.length > 0 && <div className="sources">
        {result.citations.map((source, index) => (
          <button className="source-chip" type="button" key={`${source.chunk_id}-${index}`} onClick={() => openSource(source)}>
            {index + 1}. {sourceLabel(source)}
          </button>
        ))}
      </div>}
      {options.length > 0 && <div className="options">
        {options.map((option) => (
          <button type="button" key={option} disabled={busy} onClick={() => ask(option)}
            className={/^не знаю/i.test(option) ? 'option unknown' : 'option'}>
            {/^не знаю$/i.test(option.trim()) ? 'Не знаю — показать все варианты' : option}
          </button>
        ))}
      </div>}
      {ticketId && <>
        <div className="progress" aria-label="Ход обращения">
          <span className="done"><CheckCircleIcon size={19} aria-hidden="true" />Письмо отправлено</span>
          <hr aria-hidden="true" />
          <span className={ticketPublished ? 'done' : 'now'}>
            {ticketPublished ? <CheckCircleIcon size={19} aria-hidden="true" /> : <ClockIcon size={19} aria-hidden="true" />}
            {ticketPublished ? 'Эксперт ответил' : 'Эксперт отвечает'}
          </span>
          <hr aria-hidden="true" />
          <span className={ticketPublished ? 'done' : 'todo'}><BookOpenTextIcon size={19} aria-hidden="true" />В базе знаний</span>
        </div>
        <button type="button" className="meta link-meta" onClick={() => openTicket(ticketId)}>
          Обращение <span className="nowrap">{ticketId}</span> · открыть
        </button>
      </>}
    </article>
  );
}

export function QualityReply({ content, ticketId }: { content: string; ticketId?: string }) {
  return (
    <article className="answer surface" aria-label="Ответ службы качества">
      <Tag tone="info" icon={<img src={assets.chats} width={20} height={20} alt="" />}>Ответ службы качества</Tag>
      <p className="answer-text">{content}</p>
      {ticketId && <p className="meta">Обращение {ticketId} · ответ добавлен в базу знаний</p>}
    </article>
  );
}

export function SourceDialog({ source, close }: { source: Citation; close: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { ref.current?.showModal(); }, []);
  return (
    <dialog ref={ref} className="source-dialog" onClose={close} aria-labelledby="source-title">
      <div className="source-inner">
        <button className="source-close" type="button" onClick={close} aria-label="Закрыть источник"><XIcon size={18} /></button>
        <p className="eyebrow">{source.source_type === 'qa_entry' ? 'Разъяснение службы качества' : `Пункт ${source.section}`}</p>
        <h2 id="source-title">{source.doc_title}</h2>
        {source.directorate && <p>{source.directorate}</p>}
        <blockquote>{source.quote}</blockquote>
      </div>
    </dialog>
  );
}
