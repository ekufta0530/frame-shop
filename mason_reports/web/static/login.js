'use strict';

try {
  const t = JSON.parse(localStorage.getItem('lsr.theme'));
  if (t) document.documentElement.dataset.theme = t;
} catch { /* storage unavailable */ }

const form = document.getElementById('loginForm');
const err = document.getElementById('loginError');
const btn = document.getElementById('loginBtn');

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  err.hidden = true;
  btn.disabled = true;
  btn.textContent = 'Signing in…';
  try {
    const res = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: form.username.value, password: form.password.value }),
    });
    if (res.ok) {
      let next = '';
      try { next = sessionStorage.getItem('lsr.next') || ''; sessionStorage.removeItem('lsr.next'); } catch { /* storage unavailable */ }
      location.replace('/' + next);
      return;
    }
    const data = await res.json().catch(() => ({}));
    err.textContent = data.detail || 'Sign-in failed.';
    err.hidden = false;
    form.password.value = '';
    form.password.focus();
  } catch {
    err.textContent = 'Could not reach the server. Check your connection and try again.';
    err.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Sign in';
  }
});
