const MESSAGES: Record<string, string> = {
  not_allowed: "That Google account isn't set up for this site. Ask for access, or try another account.",
  cancelled: 'Sign-in was cancelled.',
  expired: 'That sign-in attempt expired. Please try again.',
  google_failed: "Google sign-in didn't complete. Please try again.",
  not_configured: 'Sign-in is not configured on this server.',
};

export default function Login() {
  const error = MESSAGES[new URLSearchParams(location.search).get('error') ?? ''];
  let next = '/';
  try { next = sessionStorage.getItem('fs.next') || '/'; } catch { /* storage unavailable */ }

  return (
    <div className="login-wrap">
      <div className="login">
        <h1>Frame Shop Performance</h1>
        <p>Sign in with your Google account to see your stores.</p>
        {error && <div className="error">{error}</div>}
        <a className="btn primary block" href={`/api/login/google?next=${encodeURIComponent(next)}`}>
          Sign in with Google
        </a>
      </div>
    </div>
  );
}
