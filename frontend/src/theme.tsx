import { useEffect, useState } from 'react';
import { MoonIcon, SunIcon } from '@phosphor-icons/react';

export type Theme = 'light' | 'dark';

const storageKey = 'punkt.theme';
const barColor: Record<Theme, string> = { light: '#f7f8f3', dark: '#16171a' };

function storedTheme(): Theme | null {
  try {
    const value = localStorage.getItem(storageKey);
    return value === 'light' || value === 'dark' ? value : null;
  } catch {
    return null;
  }
}

function systemTheme(): Theme {
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', barColor[theme]);
}

// Тема ставится до первого рендера (main.tsx импортирует этот модуль раньше App), чтобы экран не мигал светлым.
let current: Theme = storedTheme() ?? systemTheme();
applyTheme(current);
const listeners = new Set<(theme: Theme) => void>();

function setTheme(theme: Theme, remember: boolean) {
  current = theme;
  applyTheme(theme);
  if (remember) {
    try { localStorage.setItem(storageKey, theme); } catch { /* приватный режим: тема живёт до перезагрузки */ }
  }
  listeners.forEach((listener) => listener(theme));
}

// Пока пользователь сам не выбрал тему, следуем за системной.
window.matchMedia?.('(prefers-color-scheme: dark)').addEventListener?.('change', (event) => {
  if (!storedTheme()) setTheme(event.matches ? 'dark' : 'light', false);
});

export function useTheme(): [Theme, () => void] {
  const [theme, setLocal] = useState(current);
  useEffect(() => {
    listeners.add(setLocal);
    return () => { listeners.delete(setLocal); };
  }, []);
  return [theme, () => setTheme(current === 'dark' ? 'light' : 'dark', true)];
}

export function ThemeToggle({ className = '' }: { className?: string }) {
  const [theme, toggle] = useTheme();
  const label = theme === 'dark' ? 'Включить светлую тему' : 'Включить тёмную тему';
  return (
    <button type="button" className={`theme-toggle ${className}`} onClick={toggle} aria-label={label} title={label}>
      {theme === 'dark' ? <SunIcon size={20} aria-hidden="true" /> : <MoonIcon size={20} aria-hidden="true" />}
    </button>
  );
}
