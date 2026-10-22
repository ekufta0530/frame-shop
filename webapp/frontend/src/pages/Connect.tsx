// Admin-only: how to plug the site's MCP server into Claude.
import { useState } from 'react';
import { useApi } from '../api';
import type { ConnectInfo } from '../types';

function Copy({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  return (
    <button className="btn small" type="button" onClick={() => {
      navigator.clipboard.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1500); }, () => {});
    }}>{done ? 'Copied' : label}</button>
  );
}

export default function Connect() {
  const c = useApi<ConnectInfo>('/api/connect');
  const url = c.data?.url ?? '';
  const cli = `claude mcp add --transport http frame-shop "${url}"`;
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Connect to Claude</h1>
          <div className="sub">Ask Claude questions about every store's sales and customers, using the same data as this site</div>
        </div>
      </div>
      {c.error && <div className="error">{c.error}</div>}
      {c.loading && <div className="loading">Loading…</div>}
      {c.data && !c.data.enabled && (
        <div className="banner">The Claude connector isn't switched on for this site (no MCP token is set).</div>
      )}
      {c.data?.enabled && (
        <div className="connect">
          <div className="banner">This link is a password: anyone who has it can read every store's data. Share it only with the other admins, and ask for a new one if it leaks.</div>

          <div className="card">
            <div className="card-head"><h2>Your connector link</h2><div className="right"><Copy text={url} label="Copy link" /></div></div>
            <pre className="connect-url">{url}</pre>
          </div>

          <div className="card">
            <div className="card-head"><h2>In Claude (claude.ai or the desktop app)</h2></div>
            <ol className="steps">
              <li>Open <b>Settings → Connectors</b>.</li>
              <li>Click <b>Add custom connector</b>.</li>
              <li>Name it <b>Frame Shop</b> and paste the link above as the URL.</li>
              <li>Leave the OAuth / client ID fields empty and click <b>Add</b>.</li>
              <li>In a chat, turn the connector on from the <b>+</b> menu, then ask something like <i>"How did each store do last month?"</i></li>
            </ol>
          </div>

          <div className="card">
            <div className="card-head"><h2>In Claude Code</h2><div className="right"><Copy text={cli} label="Copy command" /></div></div>
            <pre className="connect-url">{cli}</pre>
          </div>

          <div className="card">
            <div className="card-head"><h2>What Claude can do with it</h2></div>
            <ul className="steps">
              <li>List the stores and how current each one's data is.</li>
              <li>Pull the five retention KPIs, run any report the Reports tab can, or answer with read-only SQL.</li>
              <li>Cover every store at once (pooled) or a single store. On-prem stores have the full detail; cloud stores have work orders, customers, dates, status and discounts.</li>
              <li>It only reads; it never changes anything.</li>
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}
