"""Add, remove and list who may sign in to the report site (by Google account email), and what they see.

  python3 -m webapp.manage_users list
  python3 -m webapp.manage_users add jane@gmail.com --name "Jane Smith" --admin       # every store + overview
  python3 -m webapp.manage_users add owner@gmail.com --name "Owner" --store mason    # just their store(s)
  python3 -m webapp.manage_users remove jane@gmail.com

--admin / --store (repeatable) also change an existing user's access. Someone with neither can't sign in.

Works on ./users.json by default. Pass --uri gs://bucket/users.json (or set USERS_URI, as the deploy
scripts do) to edit the live file; running sites pick up changes within a minute. Removing someone ends
their session at that point.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from core.stores import load_stores
from webapp.users import ADMIN, OWNER, grants_access, normalize


def load(uri):
    if not uri.startswith('gs://'):
        p = Path(uri)
        return json.loads(p.read_text()) if p.exists() else {}
    with tempfile.TemporaryDirectory() as d:
        dest = Path(d) / 'users.json'
        r = subprocess.run(['gcloud', 'storage', 'cp', uri, str(dest)], capture_output=True, text=True)
        if r.returncode != 0:
            if 'matched no objects' in r.stderr or 'No URLs matched' in r.stderr:
                return {}
            sys.exit(r.stderr)
        return json.loads(dest.read_text())


def save(uri, data):
    text = json.dumps(data, indent=2, sort_keys=True)
    if not uri.startswith('gs://'):
        Path(uri).write_text(text)
        os.chmod(uri, 0o600)
        return
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / 'users.json'
        src.write_text(text)
        subprocess.run(['gcloud', 'storage', 'cp', str(src), uri], check=True, capture_output=True)


def describe(rec):
    if not grants_access(rec):
        return 'NO ACCESS (needs --admin or --store)'
    return 'admin: all stores' if rec['role'] == ADMIN else 'owner: ' + ', '.join(rec['stores'])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--uri', default=os.environ.get('USERS_URI', 'users.json'))
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('list')
    p = sub.add_parser('add')
    p.add_argument('email')
    p.add_argument('--name')
    access = p.add_mutually_exclusive_group()
    access.add_argument('--admin', action='store_true', help='every store, the overview and the pooled reports')
    access.add_argument('--store', action='append', metavar='ID', help='a store id from stores.toml (repeatable)')
    sub.add_parser('remove').add_argument('email')
    args = ap.parse_args()

    data = load(args.uri)
    email = normalize(getattr(args, 'email', ''))

    if args.cmd == 'list':
        for u, rec in sorted(data.items()):
            print(f"{u:35s} {rec.get('name', ''):25s} {describe(rec)}")
        if not data:
            print('(no users)')
        return
    if args.cmd == 'add':
        if '@' not in email:
            sys.exit('use the person\'s Google account email address')
        rec = data.get(email, {})
        rec['name'] = args.name or rec.get('name') or email
        if args.admin:
            rec['role'] = ADMIN
            rec.pop('stores', None)
        elif args.store:
            known = {s.id for s in load_stores()}
            if unknown := sorted(set(args.store) - known):
                sys.exit(f"no such store: {', '.join(unknown)} (stores.toml has {', '.join(sorted(known))})")
            rec['role'] = OWNER
            rec['stores'] = sorted(set(args.store))
        elif not grants_access(rec):
            sys.exit('say what they may see: --admin, or --store <id> for each of their stores')
        data[email] = rec
    else:
        if email not in data:
            sys.exit(f'no such user: {email}')
        del data[email]
    save(args.uri, data)
    print(f'{args.cmd}: {email} -> {args.uri}')


if __name__ == '__main__':
    main()
