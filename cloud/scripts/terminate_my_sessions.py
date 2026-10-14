"""One-off: clear a stale lsscloud.com session for the current user.

LifeSaver allows one active session per user. If a previous run didn't log out,
the next login is blocked with "UserAlreadyLoggedIn". Run this to terminate
YOUR OWN dangling session(s), then logins work again.

    LIFESAVER_USERNAME=... LIFESAVER_PASSWORD=... .venv/bin/python cloud/scripts/terminate_my_sessions.py

Only terminates sessions whose username matches LIFESAVER_USERNAME -- it will
never knock out another user.
"""

import contextlib
import json
import os
import re
import sys

import requests

BASE = os.environ.get("LIFESAVER_BASE_URL", "https://lsscloud.com")
USERNAME = os.environ["LIFESAVER_USERNAME"]
PASSWORD = os.environ["LIFESAVER_PASSWORD"]


def main() -> int:
    s = requests.Session()
    r = s.post(f"{BASE}/Account/Login", data={"UserName": USERNAME, "Password": PASSWORD})
    r.raise_for_status()

    m = re.search(r"var loginModel = (\{.*?\});", r.text, re.S)
    if not m:
        if "/Account/Login" not in r.url:
            print("Already logged in fine (no stale session). Nothing to do.")
            return 0
        print("Could not find loginModel in the response; login may have failed "
              "for another reason. First 500 chars of visible errors:")
        print(r.text[:500])
        return 1

    model = json.loads(m.group(1))
    errors = model.get("Errors") or []
    sessions = model.get("ActiveSessions") or []
    print(f"UserId={model.get('UserId')} Errors={errors} ActiveSessions={len(sessions)}")

    mine = [
        sess for sess in sessions
        if (sess.get("UserAccount") or {}).get("UserName", "").lower() == USERNAME.lower()
    ]
    others = len(sessions) - len(mine)
    if others:
        print(f"NOTE: {others} session(s) belong to other users on this license "
              f"pool -- leaving those alone.")

    if not mine:
        print("No session of your own to terminate.")
        return 0

    # The login page's terminate button re-submits the login form with the session's id; that ends
    # the old session and logs this one in, so log it straight back out to leave nothing open.
    ok = False
    for sess in mine:
        r = s.post(f"{BASE}/Account/Login", data={"UserName": USERNAME, "Password": PASSWORD,
                                                  "TerminateSessionId": sess["UniqueID"]})
        ok = "/Account/Login" not in r.url
        with contextlib.suppress(ValueError):
            ok = ok or bool(r.json().get("success"))
        print(f"terminate {sess['UniqueID']} (created {sess.get('DateCreated')}) -> "
              f"{'logged in' if ok else 'still blocked'}")
        if ok:
            break
    if ok:
        with contextlib.suppress(requests.RequestException):
            s.get(f"{BASE}/Account/LogOff", timeout=30)
        print("logged that session back out")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
