#!/usr/bin/env bash
# Print the MCP connector URL (https://<site>/mcp/<token>). It is a credential: anyone holding it
# can read every store's data, so share it like a password.
#   webapp/mcp_url.sh
set -euo pipefail
cd "$(dirname "$0")"
source config.sh
URL=$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format 'value(status.url)')
TOKEN=$(gcloud secrets versions access latest --secret "$MCP_TOKEN_NAME" --project "$PROJECT")
echo "$URL/mcp/$TOKEN"
