#!/usr/bin/env bash
# Manage site logins in the live users file. Changes take effect within a minute.
#   deploy/users.sh list
#   deploy/users.sh add owner@outlook.com --name "Store Owner"     # prompts for a password
#   deploy/users.sh add jane --generate                            # prints a random password
#   deploy/users.sh reset jane
#   deploy/users.sh remove jane
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/config.sh
python3 manage_users.py --uri "$USERS_URI" "$@"
