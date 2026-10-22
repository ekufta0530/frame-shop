import { StrictMode, Suspense, lazy, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Link, NavLink, Navigate, Route, Routes } from 'react-router-dom';
import { api, storage, useApi } from './api';
import { THEME_EVENT, storeColor, useColors } from './charts';
import ErrorBoundary from './ErrorBoundary';
import type { StoresResponse } from './types';
import './styles.css';

const Login = lazy(() => import('./pages/Login'));
const Overview = lazy(() => import('./pages/Overview'));
const StorePage = lazy(() => import('./pages/StorePage'));
const loading = <div className="page"><div className="loading">Loading…</div></div>;

function ThemeToggle() {
  const toggle = () => {
    const el = document.documentElement;
    const dark = el.dataset.theme ? el.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
    el.dataset.theme = dark ? 'light' : 'dark';
    storage.set('fs.theme', el.dataset.theme);
    window.dispatchEvent(new Event(THEME_EVENT));
  };
  return <button className="icon-btn" type="button" onClick={toggle} title="Switch light / dark" aria-label="Switch light or dark theme">◐</button>;
}

function UserMenu() {
  const [me, setMe] = useState<{ username: string; name: string } | null>(null);
  useEffect(() => { api<{ username: string; name: string }>('/api/me').then(setMe, () => {}); }, []);
  if (!me || me.username === 'dev') return null;
  return (
    <>
      <span className="user">{me.name}</span>
      <button className="icon-btn" type="button" onClick={async () => {
        await fetch('/api/logout', { method: 'POST' });
        location.replace('/login');
      }}>Sign out</button>
    </>
  );
}

function App() {
  const c = useColors();
  const st = useApi<StoresResponse>('/api/stores');
  const stores = st.data?.stores ?? [];
  const all = st.data?.all_stores ?? false;
  // A store owner's home is their own store; only admins get the all-stores overview.
  const home = all || !stores.length ? '/' : `/stores/${stores[0].id}`;
  const overview = (tab: 'dashboard' | 'reports') => all ? <Overview stores={stores} tab={tab} />
    : stores.length ? <Navigate to={home} replace />
      : <div className="page"><div className="error">Your account isn't linked to a store yet — ask the site admin.</div></div>;
  return (
    <>
      <header className="topbar">
        <Link to={home} className="brand"><span className="brand-mark" /><span>Frame Shop</span></Link>
        <nav className="nav" aria-label="Stores">
          {all && <NavLink to="/" end>Overview</NavLink>}
          {stores.map((s) => (
            <NavLink key={s.id} to={`/stores/${s.id}`}>
              <span className="dot" style={{ background: storeColor(c, s.slot), opacity: s.ok ? 1 : 0.35 }} />{s.name}
            </NavLink>
          ))}
        </nav>
        <div className="spacer" />
        <div className="top-right"><UserMenu /><ThemeToggle /></div>
      </header>
      {st.error && <div className="page"><div className="error">{st.error}</div></div>}
      {st.data && (
        <ErrorBoundary><Suspense fallback={loading}><Routes>
          <Route path="/" element={overview('dashboard')} />
          <Route path="/reports" element={overview('reports')} />
          <Route path="/stores/:storeId" element={<StorePage stores={stores} tab="dashboard" />} />
          <Route path="/stores/:storeId/reports" element={<StorePage stores={stores} tab="reports" />} />
          <Route path="*" element={<div className="page"><div className="error">Page not found. <Link to={home}>Back to the start</Link></div></div>} />
        </Routes></Suspense></ErrorBoundary>
      )}
      {!st.data && !st.error && loading}
    </>
  );
}

function Root() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Suspense fallback={null}><Login /></Suspense>} />
        <Route path="*" element={<App />} />
      </Routes>
    </BrowserRouter>
  );
}

createRoot(document.getElementById('root')!).render(<StrictMode><Root /></StrictMode>);
