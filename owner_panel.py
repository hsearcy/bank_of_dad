"""Small opt-in owner surface. No bank-file access or provider calls."""
from datetime import datetime, timezone
import hmac
import json
import os
import re
import sqlite3
import time

from flask import abort, g, redirect, render_template, request, session

from hosted_access import access_status, set_free_access


def owner_allowlist(value):
    """Empty disables; malformed config refuses startup rather than widening access."""
    if not value:
        return frozenset()
    try:
        entries = json.loads(value)
        if not isinstance(entries, list):
            raise ValueError()
        owners = set()
        for entry in entries:
            if (not isinstance(entry, dict) or set(entry) != {'issuer', 'subject'}
                    or entry['issuer'] != 'https://accounts.google.com'
                    or not isinstance(entry['subject'], str) or not entry['subject']
                    or len(entry['subject']) > 255 or any(c.isspace() for c in entry['subject'])):
                raise ValueError()
            owners.add((entry['issuer'], entry['subject']))
        return frozenset(owners)
    except (ValueError, TypeError):
        raise ValueError('BOD_OWNER_IDENTITIES must be a JSON list of exact Google issuer/subject pairs') from None


def date_label(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).strftime('%b %d, %Y at %H:%M UTC') if stamp else 'Not recorded'


class OwnerPanel:
    def __init__(self, host):
        self.host = host
        self.owners = owner_allowlist(os.environ.get('BOD_OWNER_IDENTITIES', ''))
        host.app.add_url_rule('/owner', 'owner_panel', self.index)
        host.app.add_url_rule('/owner/access', 'owner_access', self.access, methods=['POST'])

    def guard(self):
        if not self.owners:
            abort(404)
        with self.host.connect() as conn:
            row = self.host.family_record(conn, session.get('family'))
        # Family associations are installed ONLY by the verified OIDC callback or
        # its server-owned verified bank-choice continuation. Never trust a body,
        # email, client role, local Dad PIN or an anonymous device capability.
        if not self.host.valid_session(row, session):
            if request.endpoint == 'owner_panel' and request.method in ('GET', 'HEAD'):
                return render_template('owner.html', sign_in=True), 401
            abort(403)
        if (row['guest_hash'] is not None or 'guest' in session
                or (row['issuer'], row['subject']) not in self.owners):
            abort(403)
        g.owner_identity = (row['issuer'], row['subject'])
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            token = request.form.get('csrf_token', '')
            if (not token or not hmac.compare_digest(token.encode(), session.get('csrf', '').encode())
                    or request.headers.get('Origin') != self.host.origin):
                abort(403)
        return None

    def index(self, error=None, code=200):
        query = request.args.get('family', '').strip()
        if query and not re.fullmatch(r'[0-9a-f]{1,32}', query):
            return render_template('owner.html', invalid_search=True, query=query, families=[], has_more=False), 400
        try:
            page = max(1, int(request.args.get('page', '1')))
            if page > 1000000:
                raise ValueError()
        except ValueError:
            abort(400)
        now = time.time()
        with self.host.connect() as conn:
            # Whitelist projection: never fetch bank files, capability digests,
            # customer/Checkout URLs, or parent identities for display.
            rows = conn.execute("""SELECT id, guest_hash IS NOT NULL AS is_guest,
                paid_until, trial_started, trial_until, free_access, billing_reconciled_at,
                (SELECT created FROM access_audit WHERE family=f.id ORDER BY id DESC LIMIT 1) AS changed_at
                FROM families f WHERE id LIKE ? ORDER BY id LIMIT 51 OFFSET ?""",
                (query + '%', (page - 1) * 50)).fetchall()
            # Upgrade only this page's server-selected families, then refresh the
            # whitelisted dates; do not expose the full family record.
            upgraded = []
            for row in rows[:50]:
                current = self.host.family_record(conn, row['id'])
                upgraded.append(dict(row, trial_until=current['trial_until']))
        families = [dict(row, status=access_status(row, now)) for row in upgraded]
        return render_template('owner.html', families=families, query=query, page=page,
                               has_more=len(rows) > 50, date_label=date_label, error=error,
                               updated=date_label(now), saved=request.args.get('saved') == '1'), code

    def access(self):
        family = request.form.get('family', '')
        action = request.form.get('action', '')
        reason = request.form.get('reason', '')
        if action == 'revoke' and request.form.get('confirm_revoke') != 'yes':
            abort(400)
        try:
            with self.host.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                # Use the shared audited operation; a failed audit rolls back flag.
                set_free_access(conn, family, action, reason,
                                'oidc:' + ':'.join(g.owner_identity))
        except ValueError:
            return self.index('Use a valid family, action and a short nonempty reason.', 400)
        except LookupError:
            abort(404)
        except sqlite3.Error:
            return self.index('Nothing changed. Access could not be saved; try again.', 503)
        # GET after commit reads the exact target and its effective access afresh.
        return redirect('/owner?family=' + family + '&saved=1', code=303)
