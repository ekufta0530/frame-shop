# Deploying: MCP server, dashboard, and data refreshes

Everything lives in GCP project `mcps-507817`. All automation runs as the
`gha-deployer` service account through Workload Identity Federation (§6).

| § | What | Runs |
|---|---|---|
| 1–5 | The cloud MCP server on Cloud Run (image, secrets, deploy, verify, claude.ai) | once, by hand |
| 6 | CI/CD: push to `main` redeploys the MCP server + dashboard (`publish.yml`), plus the WIF setup every workflow uses | on push |
| 7 | Main store's daily data refresh (`refresh.yml`) | daily |
| 8 | Mason's data from Google Drive (`mason-ingest.yml`) | every 6 h |

Each § lists the one-time IAM grants it needs on top of the earlier ones. The
end-to-end picture and the bucket map are in the
[top-level README](../README.md#how-it-fits-together).

---

The MCP server is one container and one Cloud Run service, serving the MCP endpoint over
streamable-http for a **claude.ai custom connector**.

```
claude.ai  ──HTTPS + Bearer token──▶  Cloud Run (1 instance)  ──▶  lsscloud.com
                                       cloud.mcp_server.server
                                       /mcp     (MCP, auth required)
                                       /health  (public)
```

**Why one instance:** LifeSaver allows one active session per user. The server
holds one login for the life of the process and serialises report pulls. Two
instances would fight over the session, so Cloud Run must be pinned to
`--max-instances=1`.

---

## 1. Image (GitHub Actions → GHCR)

`.github/workflows/publish.yml` runs the tests, then builds and pushes on every
push to `main` and every `v*` tag:

```
ghcr.io/ekufta0530/lifesaver-mcp:latest
ghcr.io/ekufta0530/lifesaver-mcp:sha-<commit>
ghcr.io/ekufta0530/lifesaver-mcp:v1.2.3   # on tags
```

The image is built for **`linux/amd64`** (Cloud Run's architecture). On an Apple
Silicon Mac, run it locally with `--platform linux/amd64`; a native arm64 build
hits a `cryptography` SIGILL under Docker Desktop (arm64-local only — CI and
Cloud Run are unaffected). For local dev just run `python -m cloud.mcp_server.server`
directly instead of the container.

No secrets are baked into the image. **Make the GHCR package public** so Cloud
Run can pull it without registry credentials:

> GitHub → repo → Packages → `lifesaver-mcp` → Package settings → Change
> visibility → Public.

<details><summary>Private image instead (Artifact Registry mirror)</summary>

Cloud Run can't pull a private GHCR image directly. Mirror it into Artifact
Registry and deploy from there:

```bash
gcloud artifacts repositories create lifesaver --repository-format=docker --location=us
docker pull ghcr.io/ekufta0530/lifesaver-mcp:latest
docker tag  ghcr.io/ekufta0530/lifesaver-mcp:latest \
            us-docker.pkg.dev/$PROJECT/lifesaver/mcp:latest
docker push us-docker.pkg.dev/$PROJECT/lifesaver/mcp:latest
```
</details>

---

## 2. Secrets (Secret Manager)

```bash
PROJECT=your-gcp-project
gcloud config set project $PROJECT
gcloud services enable run.googleapis.com secretmanager.googleapis.com

printf '%s' 'the-lifesaver-username' | gcloud secrets create lifesaver-username --data-file=-
printf '%s' 'the-lifesaver-password' | gcloud secrets create lifesaver-password --data-file=-
openssl rand -hex 32 | tr -d '\n'    | gcloud secrets create mcp-auth-token   --data-file=-

# note the token — you'll paste it into claude.ai
gcloud secrets versions access latest --secret=mcp-auth-token; echo
```

Let the Cloud Run runtime service account read them:

```bash
PROJNUM=$(gcloud projects describe $PROJECT --format='value(projectNumber)')
for s in lifesaver-username lifesaver-password mcp-auth-token; do
  gcloud secrets add-iam-policy-binding $s \
    --member="serviceAccount:${PROJNUM}-compute@developer.gserviceaccount.com" \
    --role=roles/secretmanager.secretAccessor
done
```

---

## 3. Deploy

```bash
gcloud run deploy lifesaver-mcp \
  --image=ghcr.io/ekufta0530/lifesaver-mcp:latest \
  --region=us-central1 \
  --allow-unauthenticated \
  --port=8080 \
  --max-instances=1 \
  --min-instances=1 \
  --concurrency=8 \
  --timeout=300 \
  --cpu=1 --memory=512Mi \
  --set-secrets=LIFESAVER_USERNAME=lifesaver-username:latest,LIFESAVER_PASSWORD=lifesaver-password:latest,MCP_AUTH_TOKEN=mcp-auth-token:latest
```

- `--allow-unauthenticated` opens it at the network layer — the app's own
  Bearer check is the real gate (claude.ai can't send a Google identity token).
- `--min-instances=1` keeps the login session warm and avoids a cold-start
  re-login on every call. Drop to `0` to save cost; the first call after idle
  then takes ~5–10s to log in (it auto-clears its own stale session).
- `--max-instances=1` is **required** (session limit).
- Optional hardening: after the URL is known, add
  `--set-env-vars=MCP_ALLOWED_HOSTS=lifesaver-mcp-xxxx-uc.a.run.app` to turn on
  DNS-rebinding protection (Host/Origin allow-list).

Redeploy after a new image build — **this is automatic on every push to `main`**
(see [§6 CI/CD](#6-cicd--push-to-main)). To redeploy by hand:

```bash
gcloud run services update lifesaver-mcp --region=us-central1 \
  --image=ghcr.io/ekufta0530/lifesaver-mcp:latest
```

---

## 4. Verify

```bash
URL=$(gcloud run services describe lifesaver-mcp --region=us-central1 --format='value(status.url)')
TOK=$(gcloud secrets versions access latest --secret=mcp-auth-token)

curl -sf "$URL/health"                       # {"status":"ok"}
curl -s -o /dev/null -w '%{http_code}\n' "$URL/mcp"   # 401 (no token)

# MCP initialize handshake
curl -s "$URL/mcp" \
  -H "Authorization: Bearer $TOK" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

---

## 5. Add to claude.ai

Settings → Connectors → **Add custom connector**:

- **URL:** `https://lifesaver-mcp-xxxx-uc.a.run.app/mcp`
- **Authentication:** the connector needs to send `Authorization: Bearer <token>`
  using the `mcp-auth-token` value. If the UI only offers OAuth, use the
  "custom headers" option; otherwise this server would need an OAuth front end
  (not built).

Once connected, the `get_work_order_list_report` tool is available in chats.

---

## 6. CI/CD — push to `main`

`.github/workflows/publish.yml` runs on every push to `main`:

| Job | Trigger | Effect |
|---|---|---|
| `test` | always | `pytest -q` |
| `build-push` | always | build + push image to GHCR (`latest`, `sha-<commit>`) |
| `deploy-mcp` | push to `main` | `gcloud run services update lifesaver-mcp --image=…:latest`, then curls `/health` |
| `deploy-site` | push to `main` | builds the website image (`…-site`), then `gcloud run services update frame-shop --image=…:latest` (us-west1), then curls `/health`. See [webapp/README.md](../webapp/README.md) |

The website reads its data straight from GCS, so a deploy is a **code** change only. The daily LifeSaver pull / KPI recompute is `.github/workflows/refresh.yml` (§7 below), and Mason's upload is `mason-ingest.yml` (§8) — both separate, scheduled workflows.

### One-time setup: Workload Identity Federation

The deploy jobs authenticate to GCP keylessly. Create a deploy service account
and let this repo impersonate it:

```bash
PROJECT=mcps-507817
PROJNUM=$(gcloud projects describe $PROJECT --format='value(projectNumber)')
REPO=ekufta0530/lifesaver-mcp
SA=gha-deployer@$PROJECT.iam.gserviceaccount.com

# WIF with a service_account uses impersonation -> this API must be on, or the
# deploy jobs fail with "IAM Service Account Credentials API has not been used".
gcloud services enable iamcredentials.googleapis.com --project=$PROJECT

gcloud iam service-accounts create gha-deployer \
  --display-name="GitHub Actions deployer" --project=$PROJECT

# Cloud Run: update the service + act as its runtime SA
gcloud run services add-iam-policy-binding lifesaver-mcp --region=us-central1 \
  --member="serviceAccount:$SA" --role=roles/run.admin
gcloud iam service-accounts add-iam-policy-binding \
  ${PROJNUM}-compute@developer.gserviceaccount.com \
  --member="serviceAccount:$SA" --role=roles/iam.serviceAccountUser

# Website: roll the frame-shop Cloud Run service (us-west1) onto a new image
gcloud run services add-iam-policy-binding frame-shop --region=us-west1 \
  --member="serviceAccount:$SA" --role=roles/run.admin
gcloud iam service-accounts add-iam-policy-binding \
  frame-shop@mcps-507817.iam.gserviceaccount.com \
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
- `GCP_DEPLOY_SA` — `gha-deployer@mcps-507817.iam.gserviceaccount.com`

### Rollback

Re-run an earlier successful workflow, or pin to an older image:

```bash
gcloud run services update lifesaver-mcp --region=us-central1 \
  --image=ghcr.io/ekufta0530/lifesaver-mcp:sha-<older-commit>
```

---

## 7. Daily store refresh (`.github/workflows/refresh.yml`)

Runs on a schedule (09:17 UTC — adjust the cron to taste once you know the
stores' timezones / off-hours). It builds a matrix from `stores.toml` (every
**cloud** store, `python -m core.stores --tier cloud --matrix`) and, per store,
in parallel: load its login from Secret Manager → pull its `warehouse.db` from
GCS → `cloud.warehouse.job sync` (logs into lsscloud.com, pulls the trailing
window, recomputes KPIs) → push `warehouse.db` + any new raw files back to
`gs://lifesaver-kpi-warehouse/stores/<id>/`. Nothing is published: the website
re-reads the warehouse from GCS within ten minutes. One store failing (bad
password, lsscloud down) doesn't stop the others.

Run it by hand from Actions → **refresh store data**: `store` limits it to one
store id, `backfill` pulls that store's full history first (for a new store).

Only the **current month and quarter** move — every closed period is frozen by
`kpi_monthly.is_final` in the warehouse itself, so this can never rewrite a
report that already closed.

Each store's login is the Secret Manager secret named in `stores.toml` (default
`lifesaver-store-<id>`), holding `{"username": "...", "password": "..."}`. It
reuses the `gha-deployer` WIF identity from §6, which needs **write** access to
the warehouse bucket and **read** access to each store secret:

```bash
PROJECT=mcps-507817
SA=gha-deployer@$PROJECT.iam.gserviceaccount.com

# upgrade viewer -> objectAdmin so the workflow can write each store's warehouse back
gcloud storage buckets add-iam-policy-binding gs://lifesaver-kpi-warehouse \
  --member="serviceAccount:$SA" --role=roles/storage.objectAdmin

# one secret per cloud store (see "Adding a cloud store" in webapp/README.md)
gcloud secrets add-iam-policy-binding lifesaver-store-main \
  --member="serviceAccount:$SA" --role=roles/secretmanager.secretAccessor
```

**One-time migration of the main store** (copies; nothing is deleted) is in
[webapp/README.md](../webapp/README.md#migrating-the-main-stores-data-once).
Adding another cloud store afterwards is a `stores.toml` entry plus its secret.

---

## 8. Mason data from Google Drive (`.github/workflows/mason-ingest.yml`)

The Mason owner drops a copy of their LifeSaver folder (zipped or not) into a
Drive folder shared with `gha-deployer`. Every 6 hours the workflow runs
`sqlite_extract/ingest.sh`: newest upload → just the 9 needed `.dbf`/`.fpt`
files → `lifesaver.sqlite` → refuse it if it has fewer work orders/customers or
an older latest order date than the live one → publish to
`gs://mcps-507817-lifesaver-data/lifesaver.sqlite` (previous copy to
`archive/`, source tables to `raw/<ts>/`). The website re-reads the file within ten minutes. Uploads
changed in the last 30 min are left for the next run (still uploading). The
outcome is in `gs://mcps-507817-lifesaver-data/ingest/state.json`; a rejected
upload fails the run once, then is skipped until a new upload arrives.

One-time setup:

```bash
PROJECT=mcps-507817
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
   `gha-deployer@mcps-507817.iam.gserviceaccount.com` as Viewer**, and share it
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

---

## Rotating the token

```bash
openssl rand -hex 32 | tr -d '\n' | gcloud secrets versions add mcp-auth-token --data-file=-
gcloud run services update lifesaver-mcp --region=us-central1 \
  --update-secrets=MCP_AUTH_TOKEN=mcp-auth-token:latest
```

Then update the connector in claude.ai.
