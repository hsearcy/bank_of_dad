"""Hosted access policy and trusted local operator CLI (standard library only).

The local CLI uses filesystem authority; the optional owner UI uses verified identity.
"""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit

TRIAL_SECONDS = 7 * 24 * 60 * 60


def migrate(conn, now=None):
    """Add access fields atomically, preserving billing and every family database.

    Pre-trial families get one migration-time week, not an inferred signup date.
    The column addition and backfill commit together, so restarts never reset it.
    Caller owns commit/rollback; the writer lock also serializes concurrent startup.
    """
    conn.execute('BEGIN IMMEDIATE')
    columns = {row[1] for row in conn.execute('PRAGMA table_info(families)')}
    if 'trial_started' not in columns:
        stamp = time.time() if now is None else now
        conn.execute('ALTER TABLE families ADD COLUMN trial_started REAL NOT NULL DEFAULT 0')
        conn.execute('ALTER TABLE families ADD COLUMN trial_until REAL NOT NULL DEFAULT 0')
        conn.execute('UPDATE families SET trial_started=?, trial_until=?', (stamp, stamp + TRIAL_SECONDS))
    if 'free_access' not in columns:
        conn.execute('ALTER TABLE families ADD COLUMN free_access INTEGER NOT NULL DEFAULT 0 CHECK(free_access IN (0,1))')
    if 'billing_reconciled_at' not in columns:
        conn.execute('ALTER TABLE families ADD COLUMN billing_reconciled_at REAL')
    conn.execute('''CREATE TABLE IF NOT EXISTS access_audit (
        id INTEGER PRIMARY KEY, family TEXT NOT NULL REFERENCES families(id),
        action TEXT NOT NULL CHECK(action IN ('grant','revoke')),
        reason TEXT NOT NULL, operator TEXT NOT NULL, created REAL NOT NULL
    )''')


def access_status(row, now=None):
    now = time.time() if now is None else now
    if row['paid_until'] > now:
        kind, until = 'paid', row['paid_until']
    elif row['free_access'] == 1:
        kind, until = 'free', None
    elif row['trial_until'] > now:
        kind, until = 'trial', row['trial_until']
    else:
        kind, until = 'expired', None
    return {'kind': kind, 'active': kind != 'expired', 'until': until,
            'days_left': max(0, math.ceil((row['trial_until'] - now) / 86400)),
            'until_label': datetime.fromtimestamp(until, timezone.utc).strftime('%b %d, %Y at %H:%M UTC') if until else None}


def support_destination(value=None):
    value = os.environ.get('BOD_SUPPORT_URL', '') if value is None else value
    if not value:
        return None
    parsed = urlsplit(value)
    if (not any(c.isspace() or ord(c) < 32 for c in value)
            and ((parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password)
                 or re.fullmatch(r'mailto:[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', value))):
        return value
    raise ValueError('BOD_SUPPORT_URL must be an HTTPS contact URL or mailto:address (no credentials or whitespace)')


def set_free_access(conn, family, action, reason, operator, now=None):
    """Caller owns a writer transaction; flag and audit must commit together."""
    if not conn.in_transaction:
        raise ValueError('An explicit transaction is required')
    if not re.fullmatch(r'[0-9a-f]{32}', family) or action not in ('grant', 'revoke'):
        raise ValueError('Invalid family or action')
    reason = reason.strip()
    if not reason or len(reason) > 300:
        raise ValueError('Use an audit reason between 1 and 300 characters')
    if not conn.execute('SELECT 1 FROM families WHERE id=?', (family,)).fetchone():
        raise LookupError('Family not found')
    conn.execute('UPDATE families SET free_access=? WHERE id=?', (int(action == 'grant'), family))
    conn.execute('INSERT INTO access_audit(family, action, reason, operator, created) VALUES (?, ?, ?, ?, ?)',
                 (family, action, reason, operator, time.time() if now is None else now))


def main():
    parser = argparse.ArgumentParser(description='Local operator-only hosted free access. Verify identity with show before grant/revoke.')
    parser.add_argument('--data', required=True, help='Explicit persistent BOD_HOSTED_DATA directory; never a family bank file')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list', help='List server-owned family IDs and verified issuer/subjects')
    for action in ('show', 'grant', 'revoke'):
        sub = commands.add_parser(action)
        sub.add_argument('family', help='Exact 32-character family ID, verified with show')
        if action != 'show':
            sub.add_argument('--reason', required=True, help='Required operator audit note; do not include sensitive personal information')
    args = parser.parse_args()
    if args.command != 'list' and not re.fullmatch('[0-9a-f]{32}', args.family):
        parser.error('Invalid family ID; use list then show to verify the exact identifier')
    if args.command in ('grant', 'revoke') and not args.reason.strip():
        parser.error('A nonempty audit reason is required')
    path = Path(args.data).resolve() / 'directory.sqlite'
    try:
        # Never create or migrate a mistyped/old directory from the admin command.
        mode = 'ro' if args.command in ('list', 'show') else 'rw'
        with sqlite3.connect(path.as_uri() + '?mode=' + mode, uri=True, timeout=35) as conn:
            conn.row_factory = sqlite3.Row
            if args.command == 'list':
                rows = conn.execute('SELECT * FROM families ORDER BY id').fetchall()
            else:
                if args.command in ('grant', 'revoke'):
                    conn.execute('BEGIN IMMEDIATE')
                row = conn.execute('SELECT * FROM families WHERE id=?', (args.family,)).fetchone()
                if row is None:
                    parser.error('Family not found; nothing changed')
                if args.command in ('grant', 'revoke'):
                    set_free_access(conn, args.family, args.command, args.reason, 'uid:' + str(os.getuid()))
                    # Read back in this transaction; output only after commit succeeds.
                    row = conn.execute('SELECT * FROM families WHERE id=?', (args.family,)).fetchone()
                rows = [row]
            output = [dict(row) for row in rows]
        print(json.dumps(output, indent=2))
    except (sqlite3.Error, ValueError) as error:
        parser.error('Directory operation failed (check path, permissions and app migration): ' + str(error))


if __name__ == '__main__':
    main()
