# Shared settings for the website's deploy scripts.
PROJECT="${PROJECT:-mcps-507817}"
REGION="${REGION:-us-west1}"                        # next to the Mason data bucket
SERVICE="${SERVICE:-frame-shop}"
IMAGE="${IMAGE:-ghcr.io/ekufta0530/lifesaver-mcp-site:latest}"   # built + pushed by publish.yml
RUNTIME_SA="frame-shop@${PROJECT}.iam.gserviceaccount.com"
DATA_BUCKET="${DATA_BUCKET:-${PROJECT}-lifesaver-data}"        # lifesaver.sqlite, users.json
WAREHOUSE_BUCKET="${WAREHOUSE_BUCKET:-lifesaver-kpi-warehouse}" # stores/<id>/warehouse.db (cloud stores)
USERS_URI="gs://${DATA_BUCKET}/users.json"          # site logins (manage with webapp/users.sh)
SESSION_SECRET_NAME="lifesaver-session-secret"      # Secret Manager secret that signs login cookies
