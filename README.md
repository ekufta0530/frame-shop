# lifesaver-mcp

Sales and customer-retention tooling for picture-framing stores on Lifesaver
Software: one website for every store, fed by two kinds of data source.

| Dir | What |
|---|---|
| [`webapp/`](webapp/) | **The website** (React + FastAPI, Cloud Run): all-stores overview, a dashboard per store, and for on-prem stores an in-store detail section and a report builder / SQL console. Also **the MCP server for Claude** over every store (`/mcp/<token>`). [README](webapp/README.md) |
| [`stores.toml`](stores.toml) | Every store: id, name, tier (`cloud` / `on-prem`), and where its data lives. Adding a cloud store is an entry here + a secret |
| [`cloud/`](cloud/) | **Cloud tier.** Everything that talks to Lifesaver Cloud (`lsscloud.com`): the report scraper (`lifesaver/`), the warehouse ingest that accumulates report pulls (`warehouse/`), one-off pull scripts |
| [`sqlite_extract/`](sqlite_extract/) | **On-prem tier.** Turns a store's own LifeSaver data (uploaded to Google Drive by the owner) into `lifesaver.sqlite`, then into KPI tables -- no scraping |
| [`core/`](core/) | Store-independent: visits, the five retention KPIs, the SQLite tables every store writes, the store registry (`stores.py`) and the pooled all-stores warehouse (`aggregate.py`) |
| [`lsreports/`](lsreports/) | The query layer behind the Reports tab and the MCP tools: named dimensions/metrics over each store's data, plus the cloud and pooled report DBs (`build.py`) |
| `tests/` | Offline suite, split as `tests/{cloud,core,sqlite_extract,webapp}` |

Dependencies only point inward: `cloud` and `sqlite_extract` import `core`;
`core` imports neither. Background: [cloud/spec.md](cloud/spec.md) (phases 1-2)
and [docs/kpi-design.md](docs/kpi-design.md) (the KPI design; its renderer has
since moved into `webapp/`).

## How it fits together

```
CLOUD STORES (one lsscloud account each)        ON-PREM STORES (LifeSaver on their own machine)

lsscloud.com ── Work Order List report          owner drags LifeSaver folder / zip
   │                                               into a shared Google Drive folder
   │ refresh.yml (daily 09:17 UTC, one job         │
   │   per cloud store in stores.toml)             │ mason-ingest.yml (every 6 h)
   └─▶ cloud.warehouse.job sync                    └─▶ sqlite_extract/ingest.sh
         gs://frame-shop-511616-kpi-warehouse/                   gs://frame-shop-511616-lifesaver-data/
           stores/<id>/warehouse.db                        lifesaver.sqlite
                 │                                              │
                 └───────────────┬──────────────────────────────┘
                                 ▼   (re-read every 10 min; on-prem KPIs derived on arrival)
                       webapp (Cloud Run "frame-shop")
                         /              all stores: side by side + pooled
                         /stores/<id>   one store (+ in-store detail and Reports if on-prem)
                         /mcp/<token>   the MCP server for Claude: every store, or all pooled
```

GitHub Actions drive everything; all authenticate to GCP as `gha-deployer`
via Workload Identity Federation (no keys):

| Workflow | When | Does | Setup |
|---|---|---|---|
| [`publish.yml`](.github/workflows/publish.yml) | push to `main` | test (incl. frontend build) → website image to GHCR → redeploy the website (and with it the MCP server) | [DEPLOY.md](cloud/DEPLOY.md) §1, [webapp/README.md](webapp/README.md) |
| [`refresh.yml`](.github/workflows/refresh.yml) | daily 09:17 UTC | every cloud store: pull new report rows → recompute KPIs → save its `warehouse.db` | DEPLOY.md §2 |
| [`mason-ingest.yml`](.github/workflows/mason-ingest.yml) | every 6 h (:41) | on-prem store: newest Drive upload → validated `lifesaver.sqlite` | DEPLOY.md §3 |

GCS buckets (project `frame-shop-511616`):

| Bucket | Holds | Written by |
|---|---|---|
| `frame-shop-511616-kpi-warehouse` (private, versioned) | each cloud store's `stores/<id>/warehouse.db` + `warehouse_raw/` — the system of record | `refresh.yml` |
| `frame-shop-511616-lifesaver-data` (private) | the on-prem store's live `lifesaver.sqlite`, `archive/` (replaced copies), `raw/<ts>/` (source `.dbf`s, deleted after 365 days), `ingest/state.json`; also `users.json`, the website's logins | `mason-ingest.yml`, `webapp/users.sh` |

## Layout

| Path | What |
|---|---|
| `cloud/lifesaver/client.py` | the 3-step scrape as a class; one session, re-login + retry on expiry |
| `cloud/lifesaver/parser.py` | CSV bytes → typed rows (BOM, `$` currency, `M/d/yyyy` dates) |
| `cloud/lifesaver/reports.py` | per-report config (page path + date-field control IDs); WorkOrderList only |
| `cloud/lifesaver/models.py` | pydantic row/response schemas |
| `cloud/lifesaver/api.py` | FastAPI app (local dev; not deployed) |
| `cloud/warehouse/` | main store's ingest: raw landing → `line_items` → name-matched customers, then `core` for visits/KPIs. CLI: `python -m cloud.warehouse.job`. See [its README](cloud/warehouse/README.md) |
| `cloud/lifesaver_report_pull.py` | original standalone reference script (still runnable); also has `pull_invoice()` (single invoice as PDF) and `pull_customer_export()` (full customer list as CSV) |
| `cloud/scripts/pull_invoices.py` | bulk-pull invoices as PDFs (`invoice_<id>.pdf`), from a WorkOrderList CSV or a plain list of invoice numbers |
| `cloud/scripts/pull_customers.py` | pull the full customer list as CSV (every customer, not scoped to any date range) |
| `cloud/scripts/inspect/` | one-off recon of the other SSRS report pages (parked — see its README); also has `verify_invoice_endpoint.py` and `verify_customer_export.py`, which confirmed those two flows live before they were promoted into `lifesaver_report_pull.py` |
| `cloud/DEPLOY.md` | CI/CD, the GitHub Actions identity, and the two data workflows' setup |
| `sqlite_extract/drive.py` | finds the newest LifeSaver upload (zip or plain folder) in the shared Drive folder and downloads only what's needed |
| `sqlite_extract/extract.py` | LifeSaver `.dbf` tables (folder or zip) → `lifesaver.sqlite` (`python -m sqlite_extract.extract <dir\|zip> lifesaver.sqlite`) |
| `sqlite_extract/ingest.py` / `ingest.sh` | Drive → build → sanity-check against the live copy → publish to GCS; what `mason-ingest.yml` runs |
| `sqlite_extract/importer.py` | `lifesaver.sqlite` → `core` warehouse (`python -m sqlite_extract.importer lifesaver.sqlite mason.db`). See [its README](sqlite_extract/README.md) |
| `core/` | `visits.py`, `kpis.py`, `months.py` (pure functions), `store.py` (the shared SQLite tables), `models.py`, `config.py` |
| `webapp/` | the website: `server.py` (API), `data.py` (per-store data + pooled warehouse), `views.py`, `insights.py`, `mcp_server.py` (the MCP server for Claude), `frontend/` (React). See its [README](webapp/README.md) |
| `stores.toml`, `core/stores.py` | the store registry |
| `lsreports/` | the report query layer (`semantic.py`, `presets.py`) and the cloud/pooled report DBs (`build.py`) |
| `.github/workflows/publish.yml` | on push to `main`: test → build+push the website image to GHCR → deploy it to Cloud Run |
| `.github/workflows/refresh.yml` | daily: every cloud store → sync → recompute KPIs → save its warehouse |
| `.github/workflows/mason-ingest.yml` | every 6 h: Mason Drive upload → validated `lifesaver.sqlite` |

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt   # runtime + pytest
cp .env.example .env   # fill in LIFESAVER_USERNAME / LIFESAVER_PASSWORD
```

(The website's container installs `webapp/requirements.txt`.)

## Run the API

```bash
.venv/bin/uvicorn cloud.lifesaver.api:app --port 8000
```

- `GET /reports/work-order-list?start=2025-08-01&end=2025-08-31` → JSON
- add `&format=csv` for the raw export
- interactive docs at `http://localhost:8000/docs`

## Claude connector (MCP)

The website serves it at `/mcp/<token>`: every store, or all of them pooled, from the same data the
site shows. Tools, tiers and the connector URL: [webapp/README.md](webapp/README.md#claude-connector-mcp).

## Retention warehouse + website (Phase 3)

A cloud store (its login in `LIFESAVER_USERNAME` / `LIFESAVER_PASSWORD`; `WAREHOUSE_DB_PATH`
picks the file, default `warehouse.db`):

```bash
export LIFESAVER_USERNAME=... LIFESAVER_PASSWORD=...
.venv/bin/python -m cloud.warehouse.job backfill   # full history, month by month, resumable
.venv/bin/python -m cloud.warehouse.job kpis       # eyeball against the baselines
```

An on-prem store. In production this is hands-off: the owner uploads to Drive and
`mason-ingest.yml` does the rest. By hand:

```bash
# LifeSaver folder or zip -> lifesaver.sqlite (or: gcloud storage cp gs://frame-shop-511616-lifesaver-data/lifesaver.sqlite mason.sqlite)
.venv/bin/python -m sqlite_extract.extract ~/Downloads/LifeSaver.zip mason.sqlite
```

Then see it in the website, locally ([webapp/README.md](webapp/README.md) has the full recipe):

```bash
(cd webapp/frontend && npm install && npm run build)
STORE_MAIN_DATA=warehouse.db STORE_MASON_DATA=mason.sqlite AUTH_DISABLED=1 \
  .venv/bin/uvicorn webapp.server:app --port 8765
```

How the Drive ingest works, the `lifesaver.sqlite` data model, and the
runbook (rejected uploads, force, rollback): [sqlite_extract/README.md](sqlite_extract/README.md).

Full CLI and layer model: [cloud/warehouse/README.md](cloud/warehouse/README.md). Design,
KPI definitions, and open questions: [docs/kpi-design.md](docs/kpi-design.md).
`warehouse.db` and `warehouse_raw/` are gitignored — each cloud store's system of record is the
GCS warehouse bucket.

## Standalone script (no server)

```bash
export LIFESAVER_USERNAME=... LIFESAVER_PASSWORD=...
.venv/bin/python cloud/lifesaver_report_pull.py --start 8/1/2025 --end 8/31/2025 --out aug.csv
```

## Pulling invoices as PDFs

A single invoice (`lifesaver_report_pull.pull_invoice(session, invoice_number)`)
hits a different endpoint than WorkOrderList — `/Reports/Invoice/` needs no
postback, but the human-readable "Invoice #" must first be resolved to an
internal id via a separate POST (`resolve_invoice_id()`) before it works;
using the human number directly renders a silently-empty PDF (`$0.00`
everywhere, `#Error` in place of the invoice number). See
[cloud/spec.md](cloud/spec.md) → "Invoices — a separate, simpler flow" for how both of
these were confirmed.

Bulk-pulling, from a WorkOrderList CSV export's `invoiceNumber` column or a
plain list:

```bash
export LIFESAVER_USERNAME=... LIFESAVER_PASSWORD=...
.venv/bin/python cloud/scripts/pull_invoices.py --csv aug.csv --out-dir invoices
.venv/bin/python cloud/scripts/pull_invoices.py --ids 584,591,602 --out-dir invoices
```

Writes `invoices/invoice_<id>.pdf` per invoice, one login/session for the
whole batch, a delay between requests (`--delay`, default 1.5s), and one
invoice failing doesn't stop the rest.

## Pulling the customer list

Don't scrape it from the invoice PDFs (name + cell phone only, no
email/address, one request per invoice) — the report catalog has a report
built for exactly this. `Filter: "No filter, show all customers."` returns
every customer in the database with real columns (name, email, phone,
address, first/last purchase, total spend, ...), not scoped to any date
range. See [cloud/spec.md](cloud/spec.md) → "Customer list — a report built for exactly
this, once found" for how that was confirmed.

```bash
export LIFESAVER_USERNAME=... LIFESAVER_PASSWORD=...
.venv/bin/python cloud/scripts/pull_customers.py --out customers.csv
```

## One session per user

LifeSaver only allows one active session per user (license-limited). If a run
doesn't log out, the next login is blocked with `UserAlreadyLoggedIn`. The client
handles this automatically: it terminates **its own** stale session (never another
user's) and retries. To clear one manually:

```bash
LIFESAVER_USERNAME=... LIFESAVER_PASSWORD=... .venv/bin/python cloud/scripts/terminate_my_sessions.py
```

Set `LIFESAVER_TERMINATE_OWN_SESSION=false` to disable the auto-clear.

## Tests

```bash
.venv/bin/python -m pytest -q
```

Offline only — no network, no credentials, no GCP. The fixtures in
`tests/fixtures/` approximate the live responses; `export_sample.csv` is a real
capture. After any live run, refresh the HTML fixtures from real responses to
make the suite true regression coverage.
