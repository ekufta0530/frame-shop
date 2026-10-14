import { useEffect, useState } from 'react';

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (res.status === 401) {
    // signed out or the session expired: come back here after signing in
    try { sessionStorage.setItem('fs.next', location.pathname + location.search + location.hash); } catch { /* storage unavailable */ }
    location.replace('/login');
    throw new ApiError('Not signed in', 401);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ApiError(typeof data.detail === 'string' ? data.detail : `Request failed (${res.status})`, res.status);
  }
  return data as T;
}

/** GET a path; re-fetches when the path changes. */
export function useApi<T>(path: string | null) {
  const [state, setState] = useState<{ data: T | null; error: string | null; loading: boolean }>(
    { data: null, error: null, loading: !!path });
  useEffect(() => {
    if (!path) return;
    let live = true;
    setState((s) => ({ ...s, loading: true, error: null }));
    api<T>(path).then(
      (data) => live && setState({ data, error: null, loading: false }),
      (e: Error) => live && setState({ data: null, error: e.message, loading: false }),
    );
    return () => { live = false; };
  }, [path]);
  return state;
}

export const storage = {
  get<T>(key: string, fallback: T): T {
    try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
  },
  set(key: string, value: unknown) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage unavailable */ }
  },
};
