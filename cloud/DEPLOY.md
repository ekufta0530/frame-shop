# Deploying: the website, CI and data refreshes

Everything lives in GCP project `frame-shop-511616`. All automation runs as the
`gha-deployer` service account through Workload Identity Federation (§1).

| § | What | Runs |
|---|---|---|
| 1 | CI/CD: push to `main` redeploys the website (`publish.yml`), plus the WIF setup every workflow uses | on push |
| 2 | Cloud stores' daily data refresh (`refresh.yml`) | daily |
| 3 | Mason's data from Google Drive (`mason-ingest.yml`) | every 6 h |

The website is also the MCP server for Claude (`webapp/mcp_server.py`); its first deploy, the Google
sign-in setup and the connector URL are in [webapp/README.md](../webapp/README.md#deploy).

A new project is set up in one go by `cloud/scripts/bootstrap_project.sh` (it does the buckets, service accounts,
IAM grants and WIF below, and copies data and secrets from the old project); the sections explain what it does.
Each § lists the one-time IAM grants it needs on top of the earlier ones. The
end-to-end picture and the bucket map are in the
[top-level README](../README.md#how-it-fits-together).

---

## 1. CI/CD — push to `main`

`.github/workflows/publish.yml` runs on every push to `main`:

| Job | Trigger | Effect |
|---|---|---|
| `test` | always | `pytest -q`, plus the frontend's type-check, lint, tests and build |
| `build-push` | always | build + push the website image to GHCR (`…-site:latest`, `…-site:sha-<commit>`) |
| `deploy-site` | push to `main` | `gcloud run services update frame-shop --image=…-site:sha-<commit>` (us-west1), then curls `/health`. See [webapp/README.md](../webapp/README.md) |

The website is also the MCP server for Claude (`webapp/mcp_server.py`), so this one deploy covers both.

The website reads its data straight from GCS, so a deploy is a **code** change only. The daily LifeSaver pull / KPI recompute is `.github/workflows/refresh.yml` (§2 below), and Mason's upload is `mason-ingest.yml` (§3) — both separate, scheduled workflows.

### One-time setup: Workload Identity Federation

The deploy jobs authenticate to GCP keylessly. Create a deploy service account
and let this repo impersonate it:

```bash
PROJECT=frame-shop-511616
REPO=ekufta0530/frame-shop
SA=gha-deployer@$PROJECT.iam.gserviceaccount.com

# WIF with a service_account uses impersonation -> this API must be on, or the
# deploy jobs fail with "IAM Service Account Credentials API has not been used".
gcloud services enable iamcredentials.googleapis.com --project=$PROJECT

gcloud iam service-accounts create gha-deployer \
  --display-name="GitHub Actions deployer" --project=$PROJECT

# Website: roll the frame-shop Cloud Run service (us-west1) onto a new image
gcloud run services add-iam-policy-binding frame-shop --region=us-west1 \
  --member="serviceAccount:$SA" --role=roles/run.admin
gcloud iam service-accounts add-iam-policy-binding \
  frame-shop@frame-shop-511616.iam.gserviceaccount.com \
  --member="serviceAccount:$SA" --role=roles/iam.serviceAccountUser

# WIF pool + provider, locked to this repo
gcloud iam workload-identity-pools create github --location=global \
  --display-name="GitHub Actions"
gcloud iam workload-identity-pools providers create-oidc github \
  --location=global --workload-identity-pool=github --display-name="GitHub" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='${REPO}'" \
  --issuer-uri="https://token.actions.githubusercontent.com"

POOL=$(gcloud iam workload-identity-pools describe github --location=global --format='value(name)')
gcloud iam service-accounts add-iam-policy-binding $SA \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/${POOL}/attribute.repository/${REPO}"

# the provider resource name -> paste into GitHub as GCP_WIF_PROVIDER
gcloud iam workload-identity-pools providers describe github --location=global \
  --workload-identity-pool=github --format='value(name)'
```

Then in GitHub → repo → **Settings → Secrets and variables → Actions →
Variables**:

- `GCP_WIF_PROVIDER` — the provider resource name printed above
  (`projects/<num>/locations/global/workloadIdentityPools/github/providers/github`)
- `GCP_DEPLOY_SA` — `gha-deployer@frame-shop-511616.iam.gserviceaccount.com`

### Rollback

Re-run an earlier successful workflow, or pin to an older image:

```bash
gcloud run services update frame-shop --region=us-west1 \
  --image=ghcr.io/ekufta0530/frame-shop-site:sha-<older-commit>
```

---

## 2. Daily store refresh (`.github/workflows/refresh.yml`)

Runs on a schedule (09:17 UTC — adjust the cron to taste once you know the
stores' timezones / off-hours). It builds a matrix from `stores.toml` (every
**cloud** store, `python -m core.stores --tier cloud --matrix`) and, per store,
in parallel: load its login from Secret Manager → pull its `warehouse.db` from
GCS → `cloud.warehouse.job sync` (logs into lsscloud.com, pulls the trailing
window, recomputes KPIs) → push `warehouse.db` + any new raw files back to
`gs://frame-shop-511616-kpi-warehouse/stores/<id>/`. Nothing is published: the website
re-reads the warehouse from GCS within ten minutes. One store failing (bad
password, lsscloud down) doesn't stop the others.

Run it by hand from Actions → **refresh store data**: `store` limits it to one
store id, `backfill` pulls that store's full history first (for a new store).

Only the **current month and quarter** move — every closed period is frozen by
`kpi_monthly.is_final` in the warehouse itself, so this can never rewrite a
report that already closed.

Each store's login is the Secret Manager secret named in `stores.toml` (default
`lifesaver-store-<id>`), holding `{"username": "...", "password": "..."}`. It
reuses the `gha-deployer` WIF identity from §1, which needs **write** access to
the warehouse bucket and **read** access to each store secret:

```bash
PROJECT=frame-shop-511616
SA=gha-deployer@$PROJECT.iam.gserviceaccount.com

# upgrade viewer -> objectAdmin so the workflow can write each store's warehouse back
gcloud storage buckets add-iam-policy-binding gs://frame-shop-511616-kpi-warehouse \
  --member="serviceAccount:$SA" --role=roles/storage.objectAdmin

# one secret per cloud store (see "Adding a cloud store" in webapp/README.md)
gcloud secrets add-iam-policy-binding lifesaver-store-main \
  --member="serviceAccount:$SA" --role=roles/secretmanager.secretAccessor
```

**One-time migration of the main store** (copies; nothing is deleted) is in
[webapp/README.md](../webapp/README.md#migrating-the-main-stores-data-once).
Adding another cloud store afterwards is a `stores.toml` entry plus its secret.

---

## 3. Mason data from Google Drive (`.github/workflows/mason-ingest.yml`)

The Mason owner drops a copy of their LifeSaver folder (zipped or not) into a
Drive folder shared with `gha-deployer`. Every 6 hours the workflow runs
`sqlite_extract/ingest.sh`: newest upload → just the 9 needed `.dbf`/`.fpt`
files → `lifesaver.sqlite` → refuse it if it has fewer work orders/customers or
an older latest order date than the live one → publish to
`gs://frame-shop-511616-lifesaver-data/lifesaver.sqlite` (previous copy to
`archive/`, source tables to `raw/<ts>/`). The website re-reads the file within ten minutes. Uploads
changed in the last 30 min are left for the next run (still uploading). The
outcome is in `gs://frame-shop-511616-lifesaver-data/ingest/state.json`; a rejected
upload fails the run once, then is skipped until a new upload arrives.

One-time setup:

```bash
PROJECT=frame-shop-511616
SA=gha-deployer@$PROJECT.iam.gserviceaccount.com

gcloud services enable drive.googleapis.com --project=$PROJECT

# write lifesaver.sqlite / archive/ / raw/ / ingest/state.json
gcloud storage buckets add-iam-policy-binding gs://$PROJECT-lifesaver-data \
  --member="serviceAccount:$SA" --role=roles/storage.objectAdmin

# delete the extracted source tables after a year (they hold customer data)
echo '{"rule":[{"action":{"type":"Delete"},"condition":{"age":365,"matchesPrefix":["raw/"]}}]}' > /tmp/lc.json
gcloud storage buckets update gs://$PROJECT-lifesaver-data --lifecycle-file=/tmp/lc.json
```

Then:

1. Create a Drive folder (e.g. "LifeSaver uploads"), **share it with
   `gha-deployer@frame-shop-511616.iam.gserviceaccount.com` as Viewer**, and share it
   with the owner as Editor.
2. Set the repo variable `MASON_DRIVE_FOLDER_ID` to the folder's ID (the last
   part of its URL): `gh variable set MASON_DRIVE_FOLDER_ID --body <id>`.
3. **Actions → mason data from drive → Run workflow** once to confirm.

Owner instructions: drag the LifeSaver folder (or a zip of it) into the shared
folder. Old uploads can stay; the newest one wins.

Re-publish an upload the checks rejected (e.g. after deliberately purging old
work orders): run the workflow with **force** ticked. Roll back: copy an
`archive/lifesaver-<ts>.sqlite` over `lifesaver.sqlite`. More detail
(each step, what a red run means, the data model):
[sqlite_extract/README.md](../sqlite_extract/README.md).

Local run (your user needs `roles/iam.serviceAccountTokenCreator` on the SA to
impersonate it):

```bash
DRIVE_FOLDER_ID=<id> DRIVE_TOKEN=$(gcloud auth print-access-token \
  --impersonate-service-account=$SA --scopes=https://www.googleapis.com/auth/drive.readonly) \
  sqlite_extract/ingest.sh
```
