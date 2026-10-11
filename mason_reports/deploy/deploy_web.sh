#!/usr/bin/env bash
# First run creates the bucket, runtime service account and session secret; later runs just redeploy.
# Access control is the app's own username/password login (users.json in the bucket), so the
# Cloud Run service itself is public. Add people with deploy/users.sh before sharing the URL.
#   deploy/deploy_web.sh
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/config.sh

# New service accounts take a little while to become usable in IAM policies.
retry() { for i in 1 2 3 4 5 6 7 8; do "$@" && return 0; echo "retrying in 10s..." >&2; sleep 10; done; return 1; }

gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  storage.googleapis.com secretmanager.googleapis.com --project "$PROJECT"

gcloud storage buckets describe "gs://$BUCKET" --project "$PROJECT" >/dev/null 2>&1 || \
  gcloud storage buckets create "gs://$BUCKET" --project "$PROJECT" --location "$REGION" \
    --uniform-bucket-level-access --public-access-prevention

gcloud iam service-accounts describe "$RUNTIME_SA" --project "$PROJECT" >/dev/null 2>&1 || \
  gcloud iam service-accounts create lifesaver-reports --project "$PROJECT" --display-name "LifeSaver reports runtime"
retry gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member "serviceAccount:$RUNTIME_SA" --role roles/storage.objectViewer >/dev/null

# Dedicated build identity: newer projects give Cloud Build's default account no permissions.
gcloud iam service-accounts describe "$BUILD_SA" --project "$PROJECT" >/dev/null 2>&1 || \
  gcloud iam service-accounts create lifesaver-builder --project "$PROJECT" --display-name "LifeSaver reports build"
retry gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$BUILD_SA" \
  --role roles/cloudbuild.builds.builder --condition None >/dev/null

# Random key that signs login cookies. Rotating it (adding a new version) signs everyone out.
gcloud secrets describe "$SESSION_SECRET_NAME" --project "$PROJECT" >/dev/null 2>&1 || \
  python3 -c "import secrets; print(secrets.token_urlsafe(48), end='')" | \
    gcloud secrets create "$SESSION_SECRET_NAME" --project "$PROJECT" --data-file=- --replication-policy automatic
retry gcloud secrets add-iam-policy-binding "$SESSION_SECRET_NAME" --project "$PROJECT" \
  --member "serviceAccount:$RUNTIME_SA" --role roles/secretmanager.secretAccessor >/dev/null

gcloud run deploy "$WEB_SERVICE" --project "$PROJECT" --region "$REGION" --source . \
  --service-account "$RUNTIME_SA" --build-service-account "projects/$PROJECT/serviceAccounts/$BUILD_SA" \
  --set-env-vars "DATA_URI=$DATA_URI,USERS_URI=$USERS_URI" \
  --set-secrets "SESSION_SECRET=${SESSION_SECRET_NAME}:latest" \
  --allow-unauthenticated \
  --memory 1Gi --cpu 1 --min-instances 0 --max-instances 2 --quiet

gcloud run services describe "$WEB_SERVICE" --project "$PROJECT" --region "$REGION" --format 'value(status.url)'
