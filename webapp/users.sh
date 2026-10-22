#!/usr/bin/env bash
# Manage who may sign in (Google account emails) in the live users file. Changes take effect within a minute.
#   webapp/users.sh list
#   webapp/users.sh add owner@gmail.com --name "Store Owner" --store mason   # just their store (repeatable)
#   webapp/users.sh add partner@gmail.com --admin                           # every store + the overview
#   webapp/users.sh remove owner@gmail.com
set -euo pipefail
cd "$(dirname "$0")"
source config.sh
cd .. && python3 -m webapp.manage_users --uri "$USERS_URI" "$@"
