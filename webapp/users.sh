#!/usr/bin/env bash
# Manage site logins in the live users file. Changes take effect within a minute.
#   webapp/users.sh list
#   webapp/users.sh add owner@outlook.com --name "Store Owner"     # prompts for a password
#   webapp/users.sh add jane --generate                            # prints a random password
#   webapp/users.sh reset jane
#   webapp/users.sh remove jane
set -euo pipefail
cd "$(dirname "$0")"
source config.sh
cd .. && python3 -m webapp.manage_users --uri "$USERS_URI" "$@"
