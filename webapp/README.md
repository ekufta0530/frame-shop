# webapp — the Frame Shop website

One site for every store: **Overview** (stores side by side + all stores pooled), a **dashboard per
store**, and for on-prem stores an **in-store detail** section and a **Reports** tab (report builder +
SQL console). React + Vite frontend (`frontend/`), FastAPI backend (`server.py`), one container
(`Dockerfile`), Cloud Run service `frame-shop` in us-west1. The same service is also the **MCP server**
for Claude over every store (`/mcp/<token>`, see [Claude connector](#claude-connector-mcp)).

```
stores.toml ──▶ webapp.data.DataManager ──▶ webapp.views / insights ──▶ /api/* ──▶ React
                  │  cloud:   gs://frame-shop-511616-kpi-warehouse/stores/<id>/warehouse.db   (refresh.yml, daily)
                  │  on-prem: gs://frame-shop-511616-lifesaver-data/lifesaver.sqlite        (mason-ingest.yml)
                  ├─ + pooled "all stores" warehouse   (core.aggregate, rebuilt only when a store's data changes)
                  └─ + reports DBs: each store's + pooled (lsreports.build) ──▶ Reports tab, /mcp
```

## The two tiers

| | **cloud** | **on-prem** |
|---|---|---|
| Source | Lifesaver Cloud's Work Order List report, scraped daily | the store's own LifeSaver database files, uploaded by the owner to Drive |
| Detail | work orders only → the dashboard every store gets | the full database → the dashboard **plus** in-store detail and Reports |
| Customers | matched by name | LifeSaver customer number |
| "Today" | the clock (daily sync) | the newest order in the upload |

Both are reduced to the same `core` tables (`visits`, `customer_lifecycle`, `kpi_monthly`), so the
dashboards and the comparison treat them identically.

## Adding a cloud store

1. Add a block to [`../stores.toml`](../stores.toml):
   ```toml
   [[store]]
   id = "downtown"
   name = "Downtown"
   tier = "cloud"
   ```
   Order in the file is the order in the nav, and fixes the store's chart colour.
2. Put its lsscloud login in Secret Manager (name = `lifesaver-store-<id>`):
   ```bash
   echo -n '{"username": "...", "password": "..."}' | \
     gcloud secrets create lifesaver-store-downtown --project=frame-shop-511616 --data-file=-
   gcloud secrets add-iam-policy-binding lifesaver-store-downtown --project=frame-shop-511616 \
     --member="serviceAccount:gha-deployer@frame-shop-511616.iam.gserviceaccount.com" \
     --role=roles/secretmanager.secretAccessor
   ```
3. Run the **refresh store data** workflow once by hand with `store = downtown` and **backfill** ticked
   (pulls the full history; the daily cron takes it from there).
4. Push `stores.toml`. The site picks the store up on its next deploy; its data appears within ten
   minutes of the first refresh.

Optional keys (`chart_from`, `plot_from`, `baselines`, `outliers`, `notes`) are documented at the top
of `stores.toml`. `python -m core.stores` validates the file; CI runs the same check in the tests.

## Run it locally

```bash
.venv/bin/pip install -r webapp/requirements.txt -r requirements.txt
(cd webapp/frontend && npm install && npm run build)

# point each store at a local copy of its data (warehouse.db / lifesaver.sqlite), skip the login
STORE_MAIN_DATA=warehouse.db STORE_MASON_DATA=mason_reports/lifesaver.sqlite AUTH_DISABLED=1 \
  .venv/bin/uvicorn webapp.server:app --port 8765          # http://localhost:8765

# frontend with hot reload (proxies /api to the server above)
(cd webapp/frontend && npm run dev)
```

`STORE_<ID>_DATA` overrides one store's data location (id upper-cased, `-` → `_`).
`CACHE_DIR` is where downloaded/derived files go (default: system temp).
The MCP endpoint is `http://localhost:8765/mcp/<token>`: set `MCP_TOKEN=dev` for `/mcp/dev`, otherwise
the server logs a random one at startup.

## Deploy

CI does it: every push to `main` builds `ghcr.io/ekufta0530/lifesaver-mcp-site`, then
`deploy-site` rolls the `frame-shop` Cloud Run service onto it. One-time setup:

```bash
# 1. let the first CI run build the image, then make its package public (GitHub → Packages →
#    lifesaver-mcp-site → Package settings), like the MCP image
# 2. Google sign-in client: in the project's Console (APIs & Services -> Credentials) create an OAuth
#    "Web application" client with redirect URIs
#      http://localhost:8765/auth/callback
#      https://frame-shop-<project-number>.us-west1.run.app/auth/callback
#    (consent screen: External, scopes openid/email/profile, published), then store it:
read -s "?client id: " V; printf '%s' "$V" | gcloud secrets create lifesaver-oauth-client-id --data-file=-
read -s "?client secret: " V; printf '%s' "$V" | gcloud secrets create lifesaver-oauth-client-secret --data-file=-; unset V
# 3. create the service, its service account and bucket/secret access (creates the session secret):
webapp/deploy.sh
# 4. who may sign in -- Google account emails; add or remove people with:
webapp/users.sh add owner@gmail.com --name "Store Owner"
```

The service is public at the Cloud Run level; the gate is Google sign-in (`auth.py`: authorization-code
flow with PKCE, state and nonce, verified email) plus the email allowlist in `users.json`, with a signed
session cookie after that. There are no passwords and no password form. Everyone on the allowlist sees
every store. For local sign-in testing set `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` (and
`OAUTH_REDIRECT_URI` if not on localhost:8765) instead of `AUTH_DISABLED=1`.

### Setting up a project (or moving to a new one)

`cloud/scripts/bootstrap_project.sh` does the one-time GCP setup, safely re-runnable: APIs, service accounts,
the data and warehouse buckets, the GitHub Actions deploy identity (Workload Identity Federation), and, when the
old project is readable, a copy of the data and secrets (values go project to project, never printed):

```bash
PROJECT=frame-shop-511616 cloud/scripts/bootstrap_project.sh
```

It ends by printing what's left: the OAuth client secrets, the GitHub repo variables, the deploys, and
adding people to the allowlist.

The old public static dashboard bucket (`lifesaver-kpi-dashboard-303f74`) is no longer written to
**and still serves customer-derived figures publicly**. Close it now (the new site does not read it):

```bash
gcloud storage buckets remove-iam-policy-binding gs://lifesaver-kpi-dashboard-303f74 \
  --member=allUsers --role=roles/storage.objectViewer
gcloud storage buckets update gs://lifesaver-kpi-dashboard-303f74 --public-access-prevention
# later, once you're sure nothing needs it:  gcloud storage rm -r gs://lifesaver-kpi-dashboard-303f74
```

## Claude connector (MCP)

`mcp_server.py` serves the tools Claude uses, at `/mcp/<token>` on the same service:

| Tool | What |
|---|---|
| `list_stores` | every store, its tier, how current its data is, and what it can answer; plus `all` |
| `get_catalog(store)` | that store's datasets, dimensions, metrics and presets |
| `run_report`, `run_preset`, `find_values` | the report builder's query layer (`lsreports.semantic`) |
| `get_kpis(store)` | the dashboard's five retention KPIs by month, from the same code as the charts |
| `get_schema`, `run_sql` | read-only SQL on one store's reports DB (or `all`) |

Every tool takes `store`: an id from `stores.toml` or `all`. It only reads the data manager's copies,
never lsscloud.com, so cloud data is as fresh as the last `refresh.yml` run.

What a store can answer depends on its data (`lsreports.semantic` *levels*):

- **on-prem** queries its `lifesaver.sqlite`: everything, including salesperson, components, vendors,
  cost/margin and tax.
- **cloud** queries a copy of its `warehouse.db` with a `wo_facts` table rolled up from the Work Order
  List line items (`lsreports.build`): customers, dates, status, list price and discount. Sales =
  `retail − discount` over non-void lines, the same as the KPIs.
- **all** queries one pooled `wo_facts` with a `store` column: the fields every store has, cut off at the
  oldest store's data-through date like the pooled warehouse, customers counted per store.

Asking for a field a store lacks returns an error saying so. A store whose reports DB can't be built
keeps its dashboard and is left out of `all`.

```bash
webapp/deploy.sh                 # first run creates the token (secret frame-shop-mcp-token)
webapp/mcp_url.sh                # prints the connector URL
webapp/deploy.sh --rotate-mcp    # new token; existing connector URLs stop working
```

In claude.ai: Settings → Connectors → Add custom connector, paste the URL, leave the OAuth fields empty.
For Claude Code: `claude mcp add --transport http frame-shop "<url>"`. There is no OAuth: the URL is the
credential (it also appears in Cloud Run's request logs), so treat it like a password. Wrong or missing
tokens get a 404; without `MCP_TOKEN` on Cloud Run the endpoint is off and the site runs as usual.

## Checks

```bash
python -m pytest -q                               # backend
cd webapp/frontend && npm run check               # typecheck + lint (oxlint) + vitest
```
CI runs both before it builds an image, and deploys the image tagged with the commit (not `:latest`).

## Layout

| Path | What |
|---|---|
| `server.py` | API + serves the built frontend; payloads cached until any store's data changes |
| `data.py` | `DataManager`: per-store local copies (re-checked every 10 min, downloaded only when the GCS generation changes), on-prem warehouse derivation, the pooled warehouse; a broken store is reported and skipped |
| `views.py` | warehouse → dashboard JSON, and the overview comparison. Store-agnostic |
| `insights.py` | the on-prem "in-store detail" reports (specs run through `lsreports.semantic`) |
| `auth.py`, `users.py`, `manage_users.py`, `users.sh` | Google sign-in, the email allowlist |
| `frontend/` | React + TypeScript + Recharts. `src/charts.tsx` holds every chart; `src/styles.css` the design tokens |
| `mcp_server.py` | the MCP server for Claude, mounted at `/mcp` (see above) |
| `../lsreports/` | the query layer behind the Reports tab and the MCP tools; `build.py` makes the cloud and pooled reports DBs |
| `../core/stores.py`, `../core/aggregate.py` | the store registry and the pooled warehouse |

## Design notes

- **All stores is one pooled store, not an average.** Visits from every store go into one list (customer
  ids prefixed with the store id, so nobody merges across stores) and the same `compute_kpis` runs.
- **One common "today".** The pooled view is cut off at the *oldest* store's data-through date; the
  overview compares closed months only, so no store is compared mid-month.
- **A store that's down never takes the site down.** It shows a banner, is left out of the pool, and its
  page says why.
- **Charts** follow the dataviz skill's validated palette; a store's colour is its position in
  `stores.toml`, never its rank on a chart.
