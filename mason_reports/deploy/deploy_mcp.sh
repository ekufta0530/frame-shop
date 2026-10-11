#!/usr/bin/env bash
# Deploy the MCP server (Claude connector) as its own Cloud Run service, next to the web app.
# Run deploy_web.sh once first: this reuses its bucket, runtime service account and build account.
# Access is a random token in the connector URL (no OAuth); deploy/mcp_url.sh prints the URL.
#   deploy/deploy_mcp.sh             # first run creates the token; later runs just redeploy
#   deploy/deploy_mcp.sh --rotate    # new token: every existing connector URL stops working
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/config.sh

retry() { for i in 1 2 3 4 5 6 7 8; do "$@" && return 0; echo "retrying in 10s..." >&2; sleep 10; done; return 1; }
new_token() { python3 -c "import secrets; print(secrets.token_urlsafe(32), end='')"; }

if ! gcloud secrets describe "$MCP_TOKEN_NAME" --project "$PROJECT" >/dev/null 2>&1; then
  new_token | gcloud secrets create "$MCP_TOKEN_NAME" --project "$PROJECT" --data-file=- --replication-policy automatic
elif [[ "${1:-}" == "--rotate" ]]; then
  new_token | gcloud secrets versions add "$MCP_TOKEN_NAME" --project "$PROJECT" --data-file=-
fi
retry gcloud secrets add-iam-policy-binding "$MCP_TOKEN_NAME" --project "$PROJECT" \
  --member "serviceAccount:$RUNTIME_SA" --role roles/secretmanager.secretAccessor >/dev/null

# Same image as the web app, different command. The token is read at instance start, so a rotation
# only takes effect with this redeploy (a new revision).
gcloud run deploy "$MCP_SERVICE" --project "$PROJECT" --region "$REGION" --source . \
  --service-account "$RUNTIME_SA" --build-service-account "projects/$PROJECT/serviceAccounts/$BUILD_SA" \
  --command uvicorn --args "mcp_server.main:app,--host=0.0.0.0,--port=8080,--proxy-headers" \
  --set-env-vars "DATA_URI=$DATA_URI" \
  --set-secrets "MCP_TOKEN=${MCP_TOKEN_NAME}:latest" \
  --allow-unauthenticated \
  --memory 1Gi --cpu 1 --min-instances 0 --max-instances 2 --quiet >/dev/null

echo "Deployed. Run deploy/mcp_url.sh for the connector URL (it contains the access token)."
