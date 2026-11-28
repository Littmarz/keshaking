import chatDots from './assets/figma/chat-dots.svg';
import chats from './assets/figma/chats.svg';
import checks from './assets/figma/checks.svg';
import chevron from './assets/figma/chevron.svg';
import enter from './assets/figma/enter.svg';
import exampleArrow from './assets/figma/example-arrow.svg';
import logoDot from './assets/figma/logo-dot.svg';
import logoShape from './assets/figma/logo-shape.svg';
import sendArrow from './assets/figma/send-arrow.svg';
import shieldCheck from './assets/figma/shield-check.svg';

// Ассеты выгружены из макета Figma «ИИКУЗНИЦА» (узлы 119:33 и 135:646) и используются как есть.
export const assets = { chatDots, chats, checks, chevron, enter, exampleArrow, sendArrow, shieldCheck };

// Иконки — файлы Phosphor из проекта (src/assets/icons), цвет берут от текста через CSS-маску (см. .icon-* в styles.css).
export type IconName = 'chat-dots' | 'chats' | 'question' | 'clock' | 'check-circle' | 'checks' | 'book-open-text'
  | 'chat-text' | 'shield-check' | 'bookmarks' | 'arrow-elbow-down-left';

export function Icon({ name, size = 20, className = '' }: { name: IconName; size?: number; className?: string }) {
  return <span className={`icon icon-${name} ${className}`} aria-hidden="true" style={{ width: size, height: size }} />;
}

export function LogoMark() {
  return (
    <span className="logo-mark" aria-hidden="true">
      <img src={logoShape} width={39.75} height={33.06} alt="" />
      <img className="logo-dot" src={logoDot} width={9.7} height={9.55} alt="" />
    </span>
  );
}
