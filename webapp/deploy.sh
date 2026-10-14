#!/usr/bin/env bash
# Create or update the website's Cloud Run service from the image publish.yml builds.
# First run also creates the runtime service account and its read access to both buckets.
# Access control is Google sign-in plus an email allowlist (users.json in the data bucket), so the Cloud Run
# service is public. The OAuth client id/secret must already be in Secret Manager (see README), and people
# are added with webapp/users.sh before sharing the URL.
# The MCP server for Claude runs in the same service at /mcp/<token>; webapp/mcp_url.sh prints that URL.
#   webapp/deploy.sh
#   webapp/deploy.sh --rotate-mcp     # new MCP token: every existing connector URL stops working
set -euo pipefail
cd "$(dirname "$0")"
source config.sh

retry() { for i in 1 2 3 4 5 6 7 8; do "$@" && return 0; echo "retrying in 10s..." >&2; sleep 10; done; return 1; }

gcloud services enable run.googleapis.com secretmanager.googleapis.com --project "$PROJECT"

gcloud iam service-accounts describe "$RUNTIME_SA" --project "$PROJECT" >/dev/null 2>&1 || \
  gcloud iam service-accounts create frame-shop --project "$PROJECT" --display-name "Frame Shop website runtime"
for b in "$DATA_BUCKET" "$WAREHOUSE_BUCKET"; do
  retry gcloud storage buckets add-iam-policy-binding "gs://$b" \
    --member "serviceAccount:$RUNTIME_SA" --role roles/storage.objectViewer >/dev/null
done

# The session secret and the MCP token are created here if missing.
newsecret() { python3 -c "import secrets; print(secrets.token_urlsafe($1), end='')"; }
for sec in "$SESSION_SECRET_NAME" "$MCP_TOKEN_NAME"; do
  gcloud secrets describe "$sec" --project "$PROJECT" >/dev/null 2>&1 || \
    newsecret 48 | gcloud secrets create "$sec" --project "$PROJECT" --data-file=- --replication-policy automatic
done
if [[ "${1:-}" == "--rotate-mcp" ]]; then
  newsecret 32 | gcloud secrets versions add "$MCP_TOKEN_NAME" --project "$PROJECT" --data-file=-
fi
for sec in "$SESSION_SECRET_NAME" "$MCP_TOKEN_NAME" "${OAUTH_SECRETS[@]}"; do
  gcloud secrets describe "$sec" --project "$PROJECT" >/dev/null 2>&1 || { echo "missing secret: $sec" >&2; exit 1; }
  retry gcloud secrets add-iam-policy-binding "$sec" --project "$PROJECT" \
    --member "serviceAccount:$RUNTIME_SA" --role roles/secretmanager.secretAccessor >/dev/null
done

gcloud run deploy "$SERVICE" --project "$PROJECT" --region "$REGION" --image "$IMAGE" \
  --service-account "$RUNTIME_SA" \
  --set-env-vars "USERS_URI=$USERS_URI" \
  --set-secrets "SESSION_SECRET=${SESSION_SECRET_NAME}:latest,MCP_TOKEN=${MCP_TOKEN_NAME}:latest,GOOGLE_CLIENT_ID=${OAUTH_SECRETS[0]}:latest,GOOGLE_CLIENT_SECRET=${OAUTH_SECRETS[1]}:latest" \
  --allow-unauthenticated \
  --memory 1Gi --cpu 1 --min-instances 0 --max-instances 2 --quiet
gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format 'value(status.url)'
