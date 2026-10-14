import { useState, type FormEvent } from 'react';
import { api } from '../api';

export default function Login() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      await api('/api/login', { username, password });
      let next = '/';
      try { next = sessionStorage.getItem('fs.next') || '/'; sessionStorage.removeItem('fs.next'); } catch { /* storage unavailable */ }
      location.replace(next.startsWith('/') && !next.startsWith('/login') ? next : '/');
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <form className="login" onSubmit={submit}>
        <h1>Frame Shop Performance</h1>
        <p>Sign in to see your stores.</p>
        <label htmlFor="u">Username</label>
        <input id="u" type="text" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus required />
        <label htmlFor="pw">Password</label>
        <input id="pw" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        {error && <div className="error">{error}</div>}
        <button className="btn primary block" type="submit" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
      </form>
    </div>
  );
}
