"""Add, reset, remove and list report-site users.

  python3 manage_users.py list
  python3 manage_users.py add jane --name "Jane Smith"        # prompts for a password
  python3 manage_users.py add jane --generate                 # prints a random password
  python3 manage_users.py reset jane
  python3 manage_users.py remove jane

Works on ./users.json by default. Pass --uri gs://bucket/users.json (or set USERS_URI, as the deploy
scripts do) to edit the live file; running sites pick up changes within a minute. Resetting a password
or removing a user signs them out everywhere.
"""
import argparse
import getpass
import json
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

from lsreports.users import MIN_PASSWORD_LENGTH, hash_password


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


def ask_password(generate):
    if generate:
        pw = secrets.token_urlsafe(12)
        print(f'Password: {pw}')
        return pw
    while True:
        pw = getpass.getpass('New password: ')
        if len(pw) < MIN_PASSWORD_LENGTH:
            print(f'Use at least {MIN_PASSWORD_LENGTH} characters.')
        elif pw != getpass.getpass('Repeat password: '):
            print("Passwords don't match.")
        else:
            return pw


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--uri', default=os.environ.get('USERS_URI', 'users.json'))
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('list')
    for cmd in ('add', 'reset'):
        p = sub.add_parser(cmd)
        p.add_argument('username')
        p.add_argument('--name')
        p.add_argument('--generate', action='store_true', help='create a random password and print it')
    sub.add_parser('remove').add_argument('username')
    args = ap.parse_args()

    data = load(args.uri)
    username = getattr(args, 'username', '').strip().lower()

    if args.cmd == 'list':
        for u, rec in sorted(data.items()):
            print(f"{u:30s} {rec.get('name', '')}")
        if not data:
            print('(no users)')
        return
    if args.cmd == 'add' and username in data:
        sys.exit(f'{username} already exists; use reset to change the password')
    if args.cmd in ('reset', 'remove') and username not in data:
        sys.exit(f'no such user: {username}')

    if args.cmd == 'remove':
        del data[username]
    else:
        rec = data.get(username, {})
        rec['hash'] = hash_password(ask_password(args.generate))
        rec['name'] = args.name or rec.get('name') or username
        data[username] = rec
    save(args.uri, data)
    print(f'{args.cmd}: {username} -> {args.uri}')


if __name__ == '__main__':
    main()
