// Page-level controls shared by the home page and the store pages: the Dashboard / Reports tabs
// and the "Exclude outliers" switch that applies to every chart on the page.
import { NavLink } from 'react-router-dom';
import { useExcludeOutliers } from './outliers';

const icon = { width: 17, height: 17, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
  strokeWidth: 2, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, 'aria-hidden': true };

const DashIcon = () => (
  <svg {...icon}><rect x="3" y="3" width="7" height="9" rx="1" /><rect x="14" y="3" width="7" height="5" rx="1" />
    <rect x="14" y="12" width="7" height="9" rx="1" /><rect x="3" y="16" width="7" height="5" rx="1" /></svg>
);
const ReportIcon = () => (
  <svg {...icon}><path d="M3 3v18h18" /><path d="M7 15v2" /><path d="M11 11v6" /><path d="M15 7v10" /><path d="M19 12v5" /></svg>
);

/** Dashboard vs Reports. ``base`` is the dashboard's path; Reports lives at ``reports``. */
export function ViewTabs({ base, reports }: { base: string; reports: string }) {
  return (
    <nav className="viewtabs" aria-label="View">
      <NavLink to={base} end><DashIcon />Dashboard<span className="cap">KPIs at a glance</span></NavLink>
      <NavLink to={reports}><ReportIcon />Reports<span className="cap">Slice the data any way, save &amp; share</span></NavLink>
    </nav>
  );
}

/** Shown only when the page has outliers configured; ``labels`` names them. */
export function OutlierToggle({ labels }: { labels: string[] }) {
  const [on, set] = useExcludeOutliers();
  if (!labels.length) return null;
  return (
    <label className="switch" title={`Leave the ${labels.join(' and ')} out of every figure and chart on the site`}>
      <input type="checkbox" role="switch" checked={on} onChange={(e) => set(e.target.checked)} />
      <span className="track" aria-hidden="true" />
      <span>Exclude outliers <span className="muted">({labels.join(', ')})</span></span>
    </label>
  );
}
