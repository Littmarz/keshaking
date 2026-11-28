import { useEffect, useRef, useState } from 'react';
import { SignOutIcon, XIcon } from '@phosphor-icons/react';
import type { Account, Session } from './api';
import { assets, Icon, LogoMark } from './brand';

export type Screen = 'chat' | 'requests' | 'quality' | 'knowledge';

const dayKey = (date: Date) => `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;

function groupByDay(sessions: Session[]) {
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  const groups = new Map<string, Session[]>();
  for (const session of sessions) {
    const created = new Date(session.created_at);
    const label = dayKey(created) === dayKey(today) ? 'Сегодня' : dayKey(created) === dayKey(yesterday) ? 'Вчера' : 'Ранее';
    groups.set(label, [...(groups.get(label) ?? []), session]);
  }
  return [...groups.entries()];
}

export function Brand() {
  return <div className="brand-card surface"><LogoMark /><span className="brand-name">Пункт</span></div>;
}

export function UserCard({ user, signingOut, onSignOut, className = '' }: {
  user: Account; signingOut: boolean; onSignOut: () => void; className?: string;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const isQuality = user.role === 'quality';
  const name = isQuality ? 'Эксперт' : user.name;
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => { if (!box.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', escape); };
  }, [open]);
  return (
    <div ref={box} className={`user-wrap ${className}`}>
      {open && <div className="user-menu surface" role="menu">
        <button type="button" role="menuitem" disabled={signingOut} onClick={onSignOut}>
          <SignOutIcon size={18} aria-hidden="true" />{signingOut ? 'Выхожу…' : 'Выйти'}
        </button>
        <small>{import.meta.env.VITE_PUNKT_BUILD_VERSION ? `Сборка ${import.meta.env.VITE_PUNKT_BUILD_VERSION}` : 'Сборка без номера'}</small>
      </div>}
      <button type="button" className="user-card surface" aria-expanded={open} aria-haspopup="menu" onClick={() => setOpen((value) => !value)}>
        <span className="avatar" aria-hidden="true">{name.slice(0, 1)}</span>
        <span className="who"><strong>{name}</strong><span>{isQuality ? 'Служба качества' : 'Демо-доступ · сотрудник'}</span></span>
        <img className="chevron" src={assets.chevron} width={6.27} height={11.05} alt="" />
      </button>
    </div>
  );
}

export function Sidebar({
  user, screen, waiting, sessions, activeSession, loadingHistory, busy, signingOut, open, onClose,
  onAsk, onNavigate, onSelectChat, onSignOut,
}: {
  user: Account;
  screen: Screen;
  waiting: number;
  sessions: Session[];
  activeSession: string;
  loadingHistory: boolean;
  busy: boolean;
  signingOut: boolean;
  open: boolean;  // на телефоне сайдбар — выдвижная панель
  onClose: () => void;
  onAsk: () => void;
  onNavigate: (screen: Screen) => void;
  onSelectChat: (id: string) => void;
  onSignOut: () => void;
}) {
  const isQuality = user.role === 'quality';
  const then = (action: () => void) => () => { action(); onClose(); };
  useEffect(() => {
    if (!open) return;
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    document.addEventListener('keydown', escape);
    return () => document.removeEventListener('keydown', escape);
  }, [open, onClose]);
  // Текущий раздел — мягкая подсветка, без яркой заливки и свечения.
  const navClass = (active: boolean) => `nav-btn ${active ? 'current' : ''}`;
  return (
    <>
      {open && <div className="drawer-backdrop" onClick={onClose} aria-hidden="true" />}
      <aside id="sidebar" className={`sidebar ${open ? 'open' : ''}`} aria-label="Навигация">
        <div className="brand-row">
          <Brand />
          <button type="button" className="drawer-close mobile-only" onClick={onClose} aria-label="Закрыть меню">
            <XIcon size={22} />
          </button>
        </div>
        <div className="nav-panel surface">
          <nav className="nav" aria-label="Разделы">
            <button type="button" className={navClass(screen === 'chat')} disabled={busy && screen === 'chat'}
              aria-current={screen === 'chat' ? 'page' : undefined} onClick={then(onAsk)}>
              <Icon name="chat-dots" size={30} />Задайте вопрос
            </button>
            {isQuality ? <>
              <button type="button" className={navClass(screen === 'quality')}
                aria-current={screen === 'quality' ? 'page' : undefined} onClick={then(() => onNavigate('quality'))}>
                <Icon name="chats" size={30} />Очередь
                {waiting > 0 && <span className="nav-badge" aria-label={`Открытых: ${waiting}`}>{waiting}</span>}
              </button>
              <button type="button" className={navClass(screen === 'knowledge')}
                aria-current={screen === 'knowledge' ? 'page' : undefined} onClick={then(() => onNavigate('knowledge'))}>
                <Icon name="book-open-text" size={30} />База знаний
              </button>
            </> : (
              <button type="button" className={navClass(screen === 'requests')}
                aria-current={screen === 'requests' ? 'page' : undefined} onClick={then(() => onNavigate('requests'))}>
                <Icon name="chats" size={30} />Служба качества
                {waiting > 0 && <span className="nav-badge" aria-label={`Ждут ответа: ${waiting}`}>{waiting}</span>}
              </button>
            )}
          </nav>
          <div className="history" aria-label="История чатов">
            {sessions.length === 0 && <p className="history-empty">{loadingHistory ? 'Загружаю историю…' : 'Здесь появятся ваши чаты.'}</p>}
            {groupByDay(sessions).map(([label, items]) => (
              <section key={label}>
                <h3>{label}</h3>
                <ul>
                  {items.map((item) => (
                    <li key={item.session_id}>
                      <button type="button" className="history-item" disabled={busy} title={item.title || undefined}
                        aria-current={screen === 'chat' && activeSession === item.session_id ? 'true' : undefined}
                        onClick={then(() => onSelectChat(item.session_id))}>
                        {item.title || 'Новый чат'}
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
          <div className="inside-card">
            <strong><Icon name="shield-check" size={28} />Внутри компании</strong>
            <p>Ответы доступны только вам. Данные не покидают корпоративный контур.</p>
          </div>
        </div>
        <UserCard user={user} signingOut={signingOut} onSignOut={onSignOut} />
      </aside>
    </>
  );
}
