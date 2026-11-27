import { useEffect, useState, type FormEvent } from 'react';
import { login, request, type Account, type AuthSession } from './api';

export function SignIn({ onSignedIn, notice }: { onSignedIn: (session: AuthSession) => void; notice: string }) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [password, setPassword] = useState('');
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
    <main className="sign-in card" aria-labelledby="sign-in-title">
      <h1 id="sign-in-title">Войти в «Пункт»</h1>
      {notice && <p className="sign-in-notice" role="status">{notice}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {loading ? <p role="status">Загружаю аккаунты…</p> : accounts.length ? (
        <form onSubmit={submit} className="sign-in-form">
          <fieldset disabled={submitting}>
            <legend>Выберите аккаунт</legend>
            {accounts.map((account) => (
              <label className="account-option" key={account.user_id}>
                <input name="account" type="radio" value={account.user_id} checked={selectedId === account.user_id}
                  onChange={() => { setSelectedId(account.user_id); setPassword(''); setError(''); }} />
                <span>{account.name}</span>
              </label>
            ))}
          </fieldset>
          {selected?.password_required && (
            <label className="password-field">Пароль
              <input type="password" autoComplete="current-password" value={password} required
                disabled={submitting} onChange={(event) => setPassword(event.target.value)} />
            </label>
          )}
          <button type="submit" className="primary" disabled={submitting || !selected}>
            {submitting ? 'Вход…' : 'Войти'}
          </button>
        </form>
      ) : (
        <>
          {!error && <p>Нет доступных аккаунтов.</p>}
          <button type="button" className="secondary" onClick={() => {
            setError(''); setLoading(true); setLoadAttempt((attempt) => attempt + 1);
          }}>Повторить</button>
        </>
      )}
    </main>
  );
}
