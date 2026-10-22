import type { Unit } from './format';

export type Tier = 'cloud' | 'on-prem' | 'all';

export interface StoreInfo {
  id: string; name: string; tier: Tier; slot: number; ok: boolean; error: string | null;
  data_through: string | null; outlier_label: string;
}
// stores: only the ones this user may see; all_stores: whether they get the overview + pooled reports (admins)
export interface StoresResponse { stores: StoreInfo[]; all_stores: boolean; combined_through: string | null }

export interface KpiPoint { v: number | null; n: number | null; d: number | null; final: boolean }
export interface KpiMeta {
  key: string; label: string; tile: string; unit: Unit; good: 'up' | 'down'; kind: 'cohort' | 'trailing';
  short: string; long: string; plot_from: string; baseline: number | null;
}
export interface MonthAgg { rev: number; v: number }

export interface Dashboard {
  store: { id: string; name: string; tier: Tier; data_through: string; chart_from: string; method: string[] };
  meta: KpiMeta[];
  series: Record<string, Record<string, KpiPoint>>;
  series_ex: Record<string, Record<string, KpiPoint>>;  // replaces those series when outliers are excluded
  cohorts: { m: string; n: number }[];
  summary: { customers: number; repeat_customers: number; visits: number; revenue: number;
    work_orders: number | null; first_day: string | null; last_day: string | null };
  current_month: string;
  business: {
    monthly: Record<string, MonthAgg>;
    by_store: Record<string, Record<string, number>> | null;
    all_time_revenue: number | null; first_day: string | null;
    partial_month: string; partial_day: number; days_in_partial: number;
    partial_ly_rev: number; partial_ly_v: number; partial_ly_rev_ex: number; partial_ly_v_ex: number;
    outlier: {
      label: string; monthly: Record<string, MonthAgg>; months: string[]; total_rev: number; total_v: number;
      by_store: Record<string, Record<string, number>>;
    };
    excluded?: boolean;  // set by withoutOutliers: every figure here already has the outliers taken out
  };
  calc_version: number;
}

export interface PeriodStats {
  rev: number; v: number; new: number; rev_ex: number; v_ex: number;
  ly_rev: number; ly_v: number; ly_new: number; ly_rev_ex: number; ly_v_ex: number;
}
export interface OverviewRow {
  id: string; name: string; tier: Tier; first_day: string; outlier_label: string;
  periods: Record<'month' | 'ytd' | 'ttm', PeriodStats>;
  kpis: Record<string, number | null>;
  history: Record<string, Record<string, number | null>>;
  monthly: Record<string, MonthAgg>;
  outlier_monthly: Record<string, MonthAgg>;
  kpis_ex: Record<string, number | null>;
  history_ex: Record<string, Record<string, number | null>>;
}
export interface Overview {
  report_month: string | null; report_through: string | null; common_through: string | null;
  history_from: string | null;
  stores: OverviewRow[]; combined: OverviewRow | null;
  stale: { id: string; name: string; data_through: string; days_behind: number }[];
  unavailable: { id: string; name: string; error: string }[];
}

// ---- report builder (lsreports/semantic.py) ----
export interface Column { key: string; label: string; role: 'dimension' | 'metric'; kind?: string; format?: string }
export interface ReportResult {
  columns: Column[]; rows: unknown[][]; totals: Record<string, number | null> | null; truncated: boolean;
  sql?: string; params?: unknown[];
}
export interface Filter { field: string; op: string; value: unknown }
export interface ReportSpec {
  dataset: string; dimensions: string[]; metrics: string[]; filters?: Filter[];
  date_from?: string | null; date_to?: string | null; include_void?: boolean; limit?: number;
  sort?: { field: string; dir: 'asc' | 'desc' };
}
export interface Catalog {
  datasets: Record<string, {
    label: string; description: string;
    dimensions: Record<string, { label: string; kind: string }>;
    metrics: Record<string, { label: string; format: string }>;
  }>;
  presets: { id: string; title: string; description: string; group: string; spec: ReportSpec; chart: string }[];
  limits: string | null;  // set for a cloud store: what its data lacks
  data: { latest_order_date: string; work_orders: number };
}

export interface Insight {
  id: string; title: string; blurb: string; chart: string; metric?: string; wide?: boolean;
  spec: ReportSpec; result?: ReportResult; error?: string;
}
export interface Insights {
  work_orders: number; latest_order_date: string;
  reports: Insight[];
}

export interface ConnectInfo { enabled: boolean; url: string | null }
