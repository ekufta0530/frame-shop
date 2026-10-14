#!/usr/bin/env bash
# Pick up the Mason owner's latest LifeSaver upload from the shared Drive
# folder and, if it is new and passes the checks, publish it as the live
# lifesaver.sqlite (which the website and the SQL MCP server read).
# See sqlite_extract/ingest.py for what "new" and "passes" mean.
#
#   DRIVE_FOLDER_ID=... DRIVE_TOKEN=$(gcloud auth print-access-token ...) sqlite_extract/ingest.sh
#
# GCS layout (gs://$DATA_BUCKET):
#   lifesaver.sqlite            live DB
#   archive/lifesaver-<ts>.sqlite   the live DB each publish replaced
#   raw/<ts>/*.dbf              the source tables each publish was built from
#   ingest/state.json           last upload processed + outcome
#
# Sets published=true|false in $GITHUB_OUTPUT. Exits non-zero when an upload
# is rejected (once per upload -- state.json remembers it).
# Needs: gcloud auth with write access to the bucket. FORCE=true reprocesses.

set -euo pipefail

: "${DRIVE_FOLDER_ID:?set DRIVE_FOLDER_ID}"
: "${DRIVE_TOKEN:?set DRIVE_TOKEN (drive.readonly access token)}"
DATA_BUCKET="${DATA_BUCKET:-frame-shop-511616-lifesaver-data}"
LIVE="gs://$DATA_BUCKET/lifesaver.sqlite"
STATE="gs://$DATA_BUCKET/ingest/state.json"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
FORCE_ARG=()
[ "${FORCE:-false}" = true ] && FORCE_ARG=(--force)
published=false

if ! gcloud storage cp "$STATE" "$WORK/state.json" 2>/dev/null; then
  echo "{}" > "$WORK/state.json"
fi

echo "==> look for a new upload in Drive folder $DRIVE_FOLDER_ID"
python -m sqlite_extract.ingest check --folder "$DRIVE_FOLDER_ID" \
  --state "$WORK/state.json" --candidate "$WORK/candidate.json" ${FORCE_ARG[@]+"${FORCE_ARG[@]}"}
if [ ! -f "$WORK/candidate.json" ]; then
  echo "published=$published" >> "${GITHUB_OUTPUT:-/dev/null}"
  exit 0
fi

echo "==> build + check lifesaver.sqlite"
CURRENT_ARG=()
if gcloud storage cp "$LIVE" "$WORK/live.sqlite"; then
  CURRENT_ARG=(--current "$WORK/live.sqlite")
fi
rc=0
python -m sqlite_extract.ingest run --candidate "$WORK/candidate.json" \
  --state "$WORK/state.json" --work "$WORK/out" ${CURRENT_ARG[@]+"${CURRENT_ARG[@]}"} ${FORCE_ARG[@]+"${FORCE_ARG[@]}"} || rc=$?

if [ "$rc" -eq 0 ] && [ -f "$WORK/out/lifesaver.sqlite" ]; then
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  echo "==> publish to $LIVE"
  gcloud storage cp "$WORK/out/raw/*" "gs://$DATA_BUCKET/raw/$ts/"
  if [ ${#CURRENT_ARG[@]} -gt 0 ]; then
    gcloud storage cp "$LIVE" "gs://$DATA_BUCKET/archive/lifesaver-$ts.sqlite"
  fi
  gcloud storage cp "$WORK/out/lifesaver.sqlite" "$LIVE"
  published=true
fi

# last, so a failed publish above leaves state alone and the next run retries
gcloud storage cp "$WORK/state.json" "$STATE"
echo "published=$published" >> "${GITHUB_OUTPUT:-/dev/null}"
exit "$rc"
