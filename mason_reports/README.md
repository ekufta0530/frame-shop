# mason_reports — the SQL connector for an on-prem store

Analytics over the Mason frame shop's LifeSaver POS (Visual FoxPro) data, for Claude. One Cloud
Run service (us-west1), **`lifesaver-mcp`**: a claude.ai connector (`get_catalog`, `run_report`,
`run_preset`, `find_values`, `get_schema`, `run_sql`) over
`gs://frame-shop-511616-lifesaver-data/lifesaver.sqlite`. This is a different service from the repo's
`cloud/` MCP server, which shares the name but runs in us-central1 and pulls live from lsscloud.com.

The web report builder that used to live here is now the **Reports** tab of the website
([`../webapp/`](../webapp/)), which runs on the same query layer, [`../lsreports/`](../lsreports/).
Later, one MCP server over every store will replace this one.

The service has its own `requirements.txt` and `Dockerfile` and is not part of CI.
`deploy/deploy_mcp.sh` stages this directory plus the shared `lsreports/` package into a
build directory, so run it from anywhere. Run the commands below from this directory.

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
../lsreports/           shared query layer (also used by the website)
  db.py                 read-only SQLite access; pulls the DB from GCS when DATA_URI=gs://...
  semantic.py           named dimensions/metrics + run_report(spec)
  presets.py            ready-made reports
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
#   or: gcloud storage cp gs://frame-shop-511616-lifesaver-data/lifesaver.sqlite .
PYTHONPATH=.. MCP_TOKEN=dev uvicorn mcp_server.main:app --port 8766    # http://localhost:8766/mcp/dev
```

## Deploy (Cloud Run)

```
deploy/deploy_mcp.sh                                  # first run creates the access token; later runs redeploy
deploy/publish_data.sh /path/to/lsbackup.zip          # manual data upload -- see note below
```

`publish_data.sh` overwrites the live `lifesaver.sqlite` directly. It skips the
Drive ingest's checks (no older or smaller data) and doesn't archive the
previous copy. Prefer having the owner upload to Drive, or run the
**mason data from drive** workflow with **force** ticked.

Settings live in `deploy/config.sh` (project, region, bucket, service name). The first deploy also
needs the data bucket, runtime service account and build account; they already exist in this project.

## Claude connector (MCP server)

Tools: `get_catalog`, `run_report`, `run_preset`,
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

The service re-checks the bucket every 10 minutes, so a new upload needs no redeploy.

The data contains customer names, phones and addresses: keep the bucket private and never commit
`*.sqlite`, `*.zip` or `csv/`.
