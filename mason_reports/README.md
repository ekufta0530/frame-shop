# mason_reports — Mason's report builder + SQL MCP server

Analytics over the Mason frame shop's LifeSaver POS (Visual FoxPro) data. Two
Cloud Run services (us-west1) run from one image, and both read
`gs://mcps-507817-lifesaver-data/lifesaver.sqlite`:

- **`lifesaver-reports`**: a web report builder with its own logins, for the store.
- **`lifesaver-mcp`**: a claude.ai connector (`get_catalog`, `run_report`,
  `run_preset`, `find_values`, `get_schema`, `run_sql`). This is a different
  service from the repo's `cloud/` MCP server, which shares the name but runs in
  us-central1 and pulls live from lsscloud.com.

The services are self-contained: their own `requirements.txt` and
`Dockerfile`, no imports from the rest of the repo, and not part of CI. Run
everything below from this directory.

**Where the data comes from:** the hands-off path is the owner's Google Drive
upload. `.github/workflows/mason-ingest.yml` rebuilds and publishes
`lifesaver.sqlite` from it, so you don't need to do anything here (see
[`../sqlite_extract/README.md`](../sqlite_extract/README.md)). There is exactly
one converter from LifeSaver files to `lifesaver.sqlite`:
[`../sqlite_extract/extract.py`](../sqlite_extract/extract.py). The Drive ingest
and `deploy/publish_data.sh` both use it, so the tables and views this app
queries are defined there. A schema change goes in that file (and its tests),
then in `lsreports/semantic.py` if the app should expose it.

```
lsreports/              shared query layer used by the web app and the MCP server
  db.py                 read-only SQLite access; pulls the DB from GCS when DATA_URI=gs://...
  semantic.py           named dimensions/metrics + run_report(spec)
  presets.py            ready-made reports
  users.py              login accounts (users.json, scrypt hashes)
manage_users.py         add/reset/remove logins
web/                    FastAPI app + single-page report builder (no build step)
mcp_server/             MCP server so Claude can query the same data (claude.ai connector)
deploy/                 Cloud Run + GCS scripts
```

## Data model

- `work_order_revisions` - raw `ticket.dbf`; every save of a work order is a new row (`frevision` 1, 2, ...).
- `work_orders` - view: latest revision of each work order.
- `wo_facts` - view: `work_orders` with clean names (salesperson initials normalized, customer name from
  the customer file, turnaround days, new vs returning customer). **Start here.**
- `comp_facts` - one row per component (moulding, mat, glazing, mounting, stretching, fillet, special),
  parsed from the `FULFIELDS` memo.
- Also `customers`, `invoices`, `invoice_lines`, `payments`, `payment_detail`, `mouldings`, `mats`.

Caveat: LifeSaver only stores true cost for moulding, mat and fillet. For glazing, specials, stretching and
mounting the "cost" field is the list price, so margins are only reported for moulding/mat/fillet.

## Run locally

```
pip install -r requirements.txt
(cd .. && pip install dbfread && python3 -m sqlite_extract.extract /path/to/lsbackup.zip mason_reports/lifesaver.sqlite)
#   or: gcloud storage cp gs://mcps-507817-lifesaver-data/lifesaver.sqlite .
python3 manage_users.py add yourname                 # creates ./users.json
uvicorn web.main:app --reload --port 8765            # http://localhost:8765
```

(`AUTH_DISABLED=1` skips the login for local development.)

## Deploy (Cloud Run)

```
deploy/deploy_web.sh                                  # bucket, service account, session secret, service
deploy/publish_data.sh /path/to/lsbackup.zip          # manual data upload -- see note below
deploy/users.sh add owner@outlook.com --name "Owner"  # add a login (prompts for a password)
deploy/users.sh reset owner@outlook.com               # new password; signs them out everywhere
deploy/users.sh remove owner@outlook.com
deploy/users.sh list
```

`publish_data.sh` overwrites the live `lifesaver.sqlite` directly. It skips the
Drive ingest's checks (no older or smaller data) and doesn't archive the
previous copy. Prefer having the owner upload to Drive, or run the
**mason data from drive** workflow with **force** ticked.

Settings live in `deploy/config.sh` (project, region, bucket, service name).

Logins are the app's own usernames and passwords, stored as scrypt hashes in `users.json` in the private
bucket. Sessions last 14 days. After 5 wrong passwords a username is locked for 15 minutes (20 failures
from one IP locks that IP). The Cloud Run URL itself is public, and every page and API call except the
login page requires a session.

## Claude connector (MCP server)

A second Cloud Run service from the same image. Tools: `get_catalog`, `run_report`, `run_preset`,
`find_values`, `get_schema`, `run_sql` (read-only).

```
deploy/deploy_mcp.sh              # first run creates the access token; later runs redeploy
deploy/mcp_url.sh                 # prints the connector URL
deploy/deploy_mcp.sh --rotate     # new token; old connector URLs stop working
```

In claude.ai: Settings -> Connectors -> Add custom connector, paste the URL from `mcp_url.sh`, leave the
OAuth fields empty. For Claude Code: `claude mcp add --transport http lifesaver "<url>"`.

There is no OAuth: the URL itself (`https://.../mcp/<token>`) is the credential, so treat it like a
password. Anyone holding it can read all the data, including customer contact details. The token also
appears in Cloud Run's request logs for the project. Wrong or missing tokens get a 404.

Services re-check the bucket every 10 minutes for data and every minute for users, so neither needs a redeploy.

The data contains customer names, phones and addresses: keep the bucket private and never commit
`*.sqlite`, `*.zip` or `csv/`.
