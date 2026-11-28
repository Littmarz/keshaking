import { useEffect, useRef, useState } from 'react';
import { ListIcon, PlusIcon, SidebarSimpleIcon, XIcon } from '@phosphor-icons/react';
import { Icon } from './brand';
import { ThemeToggle } from './theme';

export function WorkspaceControls({ isQuality, navigationOpen, hasWaiting, busy, onOpenNavigation, onNewChat }: {
  isQuality: boolean;
  navigationOpen: boolean;
  hasWaiting: boolean;
  busy: boolean;
  onOpenNavigation: () => void;
  onNewChat: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menu = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const guide = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    function dismiss(event: PointerEvent) {
      if (!menu.current?.contains(event.target as Node)) setMenuOpen(false);
    }
    function escape(event: KeyboardEvent) {
      if (event.key !== 'Escape') return;
      setMenuOpen(false);
      trigger.current?.focus();
    }
    document.addEventListener('pointerdown', dismiss);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', dismiss);
      document.removeEventListener('keydown', escape);
    };
  }, [menuOpen]);

  function openGuide() {
    setMenuOpen(false);
    guide.current?.showModal();
  }

  return (
    <header className="workspace-bar">
      <div className="mobile-only workspace-actions">
        <button type="button" className="icon-btn" aria-label="Открыть навигацию" aria-expanded={navigationOpen}
          aria-controls="sidebar" onClick={onOpenNavigation}>
          <SidebarSimpleIcon size={24} aria-hidden="true" />
          {hasWaiting && <span className="dot" aria-hidden="true" />}
        </button>
        <button type="button" className="icon-btn" aria-label="Новый чат" disabled={busy} onClick={onNewChat}>
          <PlusIcon size={24} aria-hidden="true" />
        </button>
      </div>
      <div className="workspace-menu" ref={menu}>
        <button ref={trigger} type="button" className="icon-btn" aria-label="Дополнительное меню"
          title="Дополнительное меню" aria-expanded={menuOpen} aria-controls="workspace-menu-items"
          onClick={() => setMenuOpen((open) => !open)}>
          <ListIcon size={24} aria-hidden="true" />
        </button>
        <div id="workspace-menu-items" className="workspace-menu-items surface" hidden={!menuOpen}>
          <button type="button" aria-haspopup="dialog" onClick={openGuide}>
            <Icon name="book-open-text" />Руководство
          </button>
          <ThemeToggle showLabel />
        </div>
      </div>
      <dialog ref={guide} className="source-dialog" aria-labelledby="guide-title"
        onClose={() => trigger.current?.focus()}
        onClick={(event) => { if (event.target === event.currentTarget) guide.current?.close(); }}>
        <div className="source-inner guide-inner">
          <button type="button" className="source-close" aria-label="Закрыть руководство"
            onClick={() => guide.current?.close()}><XIcon size={18} aria-hidden="true" /></button>
          <h2 id="guide-title">Руководство</h2>
          <ol className="guide-steps">
            <li><strong>Задайте вопрос о рабочем процессе.</strong> Укажите дирекцию и этап, если знаете их.</li>
            <li><strong>Уточните детали.</strong> Выберите предложенный вариант или напишите ответ. Нажмите на номер источника, чтобы открыть пункт регламента.</li>
            {isQuality ? <>
              <li><strong>Откройте «Очередь».</strong> Выберите обращение, напишите разъяснение и при необходимости укажите регламент и пункт.</li>
              <li><strong>Опубликуйте ответ.</strong> Сохраните черновик для проверки или нажмите «Опубликовать в базу». Сотрудник увидит ответ в чате, а разъяснение появится в базе знаний.</li>
            </> : <>
              <li><strong>Следите за обращением.</strong> Когда ответ нужно уточнить у эксперта, статус и опубликованное разъяснение появятся в разделе «Служба качества».</li>
              <li><strong>Вернитесь к диалогу.</strong> Откройте нужный чат в истории или нажмите «Задайте вопрос», чтобы начать новый.</li>
            </>}
          </ol>
        </div>
      </dialog>
    </header>
  );
}
