#!/usr/bin/env bash
# Manage who may sign in (Google account emails) in the live users file. Changes take effect within a minute.
# Run with -h (or nothing) for the cheat sheet.
set -euo pipefail
cd "$(dirname "$0")"

if [[ $# -eq 0 || "$1" == "-h" || "$1" == "--help" || "$1" == "help" ]]; then
  stores=$(cd .. && python3 -c "from core.stores import load_stores; print('\n'.join(f'  {s.id:10s} {s.name}' for s in load_stores()))")
  cat <<EOF
Who can sign in to the Frame Shop site, and what they see. Changes go live within a minute.

  webapp/users.sh list                                          see everyone
  webapp/users.sh add EMAIL --name "Name" --store STORE_ID      a store owner: just that store
  webapp/users.sh add EMAIL --name "Name" --admin               everything, including the overview
  webapp/users.sh remove EMAIL                                  take away access

EMAIL is their Google account. Running "add" on someone who's already there changes their access.
Give --store more than once for an owner with several stores.

Store ids:
$stores
EOF
  exit 0
fi

source config.sh
cd .. && python3 -m webapp.manage_users --uri "$USERS_URI" "$@"
