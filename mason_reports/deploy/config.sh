# Shared settings for the deploy scripts. Edit these before the first deploy.
PROJECT="${PROJECT:-frame-shop-511616}"                   # frame-shop
REGION="${REGION:-us-west1}"
BUCKET="${BUCKET:-${PROJECT}-lifesaver-data}"      # private bucket holding lifesaver.sqlite
RUNTIME_SA="lifesaver-reports@${PROJECT}.iam.gserviceaccount.com"
BUILD_SA="lifesaver-builder@${PROJECT}.iam.gserviceaccount.com"   # Cloud Build runs as this
DATA_URI="gs://${BUCKET}/lifesaver.sqlite"
MCP_SERVICE="${MCP_SERVICE:-lifesaver-mcp}"
MCP_TOKEN_NAME="lifesaver-mcp-token"                # Secret Manager secret in the MCP connector URL
