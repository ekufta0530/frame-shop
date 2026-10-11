#!/usr/bin/env bash
# Rebuild lifesaver.sqlite from a LifeSaver backup and upload it. Running services pick it
# up within REFRESH_SECONDS (10 min) - no redeploy needed.
#   deploy/publish_data.sh /path/to/lsbackup.zip      (or a copy of the LifeSaver folder)
# Uses the repo's shared converter (sqlite_extract/extract.py), the same one the Drive ingest
# runs, so this app and that pipeline always build the same tables. Needs dbfread installed
# (the repo root's requirements.txt). Unlike the Drive ingest, this skips the "not older or
# smaller than live" checks and doesn't archive the copy it replaces.
set -euo pipefail
SRC="${1:?usage: publish_data.sh <lsbackup.zip | LifeSaver dir>}"
SRC="$(cd "$(dirname "$SRC")" && pwd)/$(basename "$SRC")"   # before the cd below
cd "$(dirname "$0")/.."
source deploy/config.sh
OUT="$(mktemp -d)/lifesaver.sqlite"
(cd .. && python3 -m sqlite_extract.extract "$SRC" "$OUT")
gcloud storage cp "$OUT" "$DATA_URI"
echo "Published to $DATA_URI"
