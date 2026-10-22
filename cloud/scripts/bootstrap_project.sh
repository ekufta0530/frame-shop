#!/usr/bin/env bash
# One-time (and safe to re-run) setup of a GCP project to host everything: the website (which is also the
# MCP server for Claude), the data buckets and the GitHub Actions deploy identity. Copies the data and secrets from the old project
# when OLD_PROJECT is readable. Nothing in the old project is changed.
#
#   PROJECT=frame-shop-511616 cloud/scripts/bootstrap_project.sh
#   PROJECT=... cloud/scripts/bootstrap_project.sh --skip-copy     # new buckets/identities only
#
# Needs: you are an Owner of PROJECT, billing is linked, and (to copy) you can read OLD_PROJECT.
# Not done here (prints what's left at the end): the OAuth client secrets, the website deploy,
# the GitHub repo variables, sharing the Mason Drive folder with the new deploy account.
set -euo pipefail

PROJECT="${PROJECT:?set PROJECT=<new project id>}"
OLD_PROJECT="${OLD_PROJECT:-mcps-507817}"
REPO="${REPO:-ekufta0530/frame-shop}"
REGION="${REGION:-us-west1}"
DATA_BUCKET="${DATA_BUCKET:-${PROJECT}-lifesaver-data}"
WAREHOUSE_BUCKET="${WAREHOUSE_BUCKET:-${PROJECT}-kpi-warehouse}"
OLD_DATA_BUCKET="${OLD_DATA_BUCKET:-${OLD_PROJECT}-lifesaver-data}"
OLD_WAREHOUSE_BUCKET="${OLD_WAREHOUSE_BUCKET:-lifesaver-kpi-warehouse}"
# secrets carried over unchanged: the cloud stores' lsscloud logins
COPY_SECRETS=(lifesaver-store-main)
SKIP_COPY=false; [[ "${1:-}" == "--skip-copy" ]] && SKIP_COPY=true

DEPLOYER="gha-deployer@${PROJECT}.iam.gserviceaccount.com"
SITE_SA="frame-shop@${PROJECT}.iam.gserviceaccount.com"            # website runtime

say() { printf '\n== %s\n' "$*"; }
retry() { for i in 1 2 3 4 5 6 7 8; do "$@" && return 0; echo "  retrying in 10s..." >&2; sleep 10; done; return 1; }
G() { gcloud --project "$PROJECT" "$@"; }

say "Project $PROJECT (copying from $OLD_PROJECT: $([[ $SKIP_COPY == true ]] && echo no || echo yes))"
G projects describe "$PROJECT" --format='value(projectNumber)' >/dev/null
PROJNUM=$(G projects describe "$PROJECT" --format='value(projectNumber)')
[[ "$PROJECT" != "$OLD_PROJECT" ]] || { echo "PROJECT is the old project; refusing" >&2; exit 1; }

say "APIs"
G services enable run.googleapis.com secretmanager.googleapis.com storage.googleapis.com \
  iam.googleapis.com iamcredentials.googleapis.com drive.googleapis.com

say "Service accounts"
for spec in "gha-deployer:GitHub Actions deployer" "frame-shop:Frame Shop website runtime"; do
  id="${spec%%:*}"
  G iam service-accounts describe "${id}@${PROJECT}.iam.gserviceaccount.com" >/dev/null 2>&1 || \
    G iam service-accounts create "$id" --display-name "${spec#*:}"
done

say "Buckets"
make_bucket() {
  G storage buckets describe "gs://$1" >/dev/null 2>&1 || \
    G storage buckets create "gs://$1" --location "$REGION" --uniform-bucket-level-access --public-access-prevention
}
make_bucket "$DATA_BUCKET"
make_bucket "$WAREHOUSE_BUCKET"
# the extracted Mason source tables hold customer data: delete them after a year
lc="$(mktemp)"; trap 'rm -f "$lc"' EXIT
echo '{"rule":[{"action":{"type":"Delete"},"condition":{"age":365,"matchesPrefix":["raw/"]}}]}' > "$lc"
G storage buckets update "gs://$DATA_BUCKET" --lifecycle-file="$lc"

if [[ $SKIP_COPY == false ]]; then
  say "Copy data from $OLD_PROJECT (existing files are kept, not overwritten)"
  gcloud storage cp --no-clobber "gs://$OLD_DATA_BUCKET/lifesaver.sqlite" "gs://$DATA_BUCKET/lifesaver.sqlite" \
    || echo "  (no lifesaver.sqlite copied; the Mason ingest will publish one)"
  # users.json is NOT copied: it holds the old password hashes. Sign-in is by Google account now.
  gcloud storage cp -r --no-clobber "gs://$OLD_WAREHOUSE_BUCKET/stores" "gs://$WAREHOUSE_BUCKET/" \
    || echo "  (no warehouses copied; run refresh.yml with backfill)"

  say "Copy secrets from $OLD_PROJECT (values go straight from one project to the other, never printed)"
  for s in "${COPY_SECRETS[@]}"; do
    if G secrets describe "$s" >/dev/null 2>&1; then echo "  $s: already exists"; continue; fi
    if gcloud secrets versions access latest --secret "$s" --project "$OLD_PROJECT" 2>/dev/null | \
         G secrets create "$s" --data-file=- --replication-policy automatic >/dev/null 2>&1; then
      echo "  $s: copied"
    else
      echo "  $s: NOT copied (missing in $OLD_PROJECT?) -- create it by hand"
      G secrets delete "$s" --quiet >/dev/null 2>&1 || true   # drop an empty shell left by a failed pipe
    fi
  done
fi

say "Access"
bucket_role() { retry G storage buckets add-iam-policy-binding "gs://$1" --member "serviceAccount:$2" --role "$3" >/dev/null; }
secret_role() { G secrets describe "$1" >/dev/null 2>&1 && \
  retry G secrets add-iam-policy-binding "$1" --member "serviceAccount:$2" --role roles/secretmanager.secretAccessor >/dev/null || true; }

bucket_role "$DATA_BUCKET" "$DEPLOYER" roles/storage.objectAdmin        # mason-ingest publishes here
bucket_role "$WAREHOUSE_BUCKET" "$DEPLOYER" roles/storage.objectAdmin   # refresh.yml writes warehouses
bucket_role "$DATA_BUCKET" "$SITE_SA" roles/storage.objectViewer
bucket_role "$WAREHOUSE_BUCKET" "$SITE_SA" roles/storage.objectViewer

for s in $(G secrets list --format='value(name)' --filter='name~lifesaver-store-'); do secret_role "$s" "$DEPLOYER"; done

# the deploy account rolls the website onto new images and acts as its runtime account
retry G projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$DEPLOYER" \
  --role roles/run.admin --condition=None >/dev/null
retry G iam service-accounts add-iam-policy-binding "$SITE_SA" --member "serviceAccount:$DEPLOYER" \
  --role roles/iam.serviceAccountUser >/dev/null

say "GitHub Actions identity (Workload Identity Federation, locked to $REPO)"
G iam workload-identity-pools describe github --location=global >/dev/null 2>&1 || \
  G iam workload-identity-pools create github --location=global --display-name "GitHub Actions"
G iam workload-identity-pools providers describe github --location=global --workload-identity-pool=github >/dev/null 2>&1 || \
  G iam workload-identity-pools providers create-oidc github --location=global --workload-identity-pool=github \
    --display-name "GitHub" \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository" \
    --attribute-condition "assertion.repository=='${REPO}'" \
    --issuer-uri "https://token.actions.githubusercontent.com"
POOL=$(G iam workload-identity-pools describe github --location=global --format='value(name)')
retry G iam service-accounts add-iam-policy-binding "$DEPLOYER" --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/${POOL}/attribute.repository/${REPO}" >/dev/null
PROVIDER=$(G iam workload-identity-pools providers describe github --location=global \
  --workload-identity-pool=github --format='value(name)')

cat <<EOF

== Done. What's left (in this order)

1. OAuth client secrets (once): lifesaver-oauth-client-id / lifesaver-oauth-client-secret in this project
   (README step 2). webapp/deploy.sh refuses to run without them.

2. GitHub repo variables (Settings -> Secrets and variables -> Actions -> Variables), or:
     gh variable set GCP_WIF_PROVIDER --body '$PROVIDER'
     gh variable set GCP_DEPLOY_SA   --body '$DEPLOYER'

3. Mason Drive folder: share it with $DEPLOYER as Viewer (the old deploy account's share doesn't carry over).

4. Deploy the website (also creates the MCP token):
     PROJECT=$PROJECT webapp/deploy.sh          # -> https://frame-shop-${PROJNUM}.${REGION}.run.app

5. People: webapp/users.sh add someone@gmail.com --name "Name"     (one per person)
6. Claude connector: PROJECT=$PROJECT webapp/mcp_url.sh prints its URL (a new project means a new token).
EOF
