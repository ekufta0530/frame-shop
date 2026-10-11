#!/usr/bin/env bash
# Print the Claude connector URL. It contains the access token: treat it like a password.
#   deploy/mcp_url.sh
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/config.sh
BASE=$(gcloud run services describe "$MCP_SERVICE" --project "$PROJECT" --region "$REGION" --format 'value(status.url)')
TOKEN=$(gcloud secrets versions access latest --secret "$MCP_TOKEN_NAME" --project "$PROJECT")
echo "$BASE/mcp/$TOKEN"
