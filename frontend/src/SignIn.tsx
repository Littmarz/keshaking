import { useEffect, useState, type FormEvent } from 'react';
import { EyeIcon, EyeSlashIcon } from '@phosphor-icons/react';
import { login, request, type Account, type AuthSession } from './api';
import { LogoMark } from './brand';
import { ThemeToggle } from './theme';

export function SignIn({ onSignedIn, notice }: { onSignedIn: (session: AuthSession) => void; notice: string }) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const selected = accounts.find((account) => account.user_id === selectedId);

  useEffect(() => {
    const controller = new AbortController();
    async function loadAccounts() {
      try {
        const available = await request<Account[]>('/auth/users', undefined, undefined, controller.signal);
        if (!controller.signal.aborted) {
          setAccounts(available);
          setSelectedId(available[0]?.user_id ?? '');
        }
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Вход сейчас недоступен.');
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void loadAccounts();
    return () => controller.abort();
  }, [loadAttempt]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected || submitting) return;
    setSubmitting(true);
    setError('');
    try {
      onSignedIn(await login(selected.user_id, selected.password_required ? password : undefined));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось войти.');
      setSubmitting(false);
    }
  }

  return (
    <div className="signin-page">
      <ThemeToggle className="signin-theme" />
      <main className="signin surface" aria-labelledby="sign-in-title">
        <LogoMark />
        <h1 id="sign-in-title">Вход в Пункт</h1>
        <p className="sub">Ответы по регламентам с точным пунктом</p>
        {notice && <p className="notice" role="status">{notice}</p>}
        {loading ? <p className="notice" role="status">Загружаю аккаунты…</p> : accounts.length ? (
          <form onSubmit={submit}>
            <fieldset className="segmented" disabled={submitting}>
              <legend>Роль</legend>
              {accounts.map((account) => (
                <label key={account.user_id}>
                  <input name="account" type="radio" value={account.user_id} checked={selectedId === account.user_id}
                    onChange={() => { setSelectedId(account.user_id); setPassword(''); setShowPassword(false); setError(''); }} />
                  {account.name}
                </label>
              ))}
            </fieldset>
            {selected?.password_required && (
              <>
                <label className="sr-only" htmlFor="sign-in-password">Пароль</label>
                <div className="password-field">
                  <input id="sign-in-password" className="field" type={showPassword ? 'text' : 'password'} autoComplete="current-password"
                    placeholder="Пароль" value={password} required disabled={submitting} onChange={(event) => setPassword(event.target.value)} />
                  <button type="button" className="password-toggle" disabled={submitting} aria-controls="sign-in-password"
                    aria-pressed={showPassword} aria-label={showPassword ? 'Скрыть пароль' : 'Показать пароль'}
                    title={showPassword ? 'Скрыть пароль' : 'Показать пароль'} onClick={() => setShowPassword((shown) => !shown)}>
                    {showPassword ? <EyeSlashIcon size={22} aria-hidden="true" /> : <EyeIcon size={22} aria-hidden="true" />}
                  </button>
                </div>
              </>
            )}
            {error && <p className="alert" role="alert">{error}</p>}
            <button type="submit" className="submit" disabled={submitting || !selected}>{submitting ? 'Вход…' : 'Войти'}</button>
          </form>
        ) : (
          <>
            {error ? <p className="alert" role="alert">{error}</p> : <p className="notice">Нет доступных аккаунтов.</p>}
            <button type="button" className="submit retry" onClick={() => {
              setError(''); setLoading(true); setLoadAttempt((attempt) => attempt + 1);
            }}>Повторить</button>
          </>
        )}
      </main>
    </div>
  );
}
