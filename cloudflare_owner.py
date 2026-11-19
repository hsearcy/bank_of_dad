"""Private owner controls on the integrated Worker; no provider or ledger access.

Directory authenticates the live family session, not an email/client role. Family
DO commits the override and its audit together, independently of Stripe writes.
"""
import asyncio
import json
import re
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from jinja2 import DictLoader, Environment
from cloudflare_auth import COOKIE, cookie, response
from cloudflare_templates import TEMPLATES
from hosted_access import access_status
from owner_panel import date_label, owner_allowlist

REGISTRY = '''CREATE TABLE IF NOT EXISTS owner_families (
 family TEXT PRIMARY KEY
)'''
AUDIT = '''CREATE TABLE IF NOT EXISTS _bod_access_audit (
 id INTEGER PRIMARY KEY, family TEXT NOT NULL,
 action TEXT NOT NULL CHECK(action IN ('grant','revoke')),
 reason TEXT NOT NULL, operator TEXT NOT NULL, created REAL NOT NULL,
 previous INTEGER NOT NULL, current INTEGER NOT NULL
)'''


def owners(env):
    return owner_allowlist(str(getattr(env, 'BOD_OWNER_IDENTITIES', '')))


class FamilyOwner:
    async def owner(self, payload):
        """Binding-only RPC; never expose arbitrary RPC dispatch over HTTP."""
        data = json.loads(payload)
        actor = (data['issuer'], data['subject'])
        if actor not in owners(self.env):
            raise ValueError('Owner denied')
        def execute(c):
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='_bod_family'").fetchone():
                return None  # Never bootstrap a guessed family.
            row = self.billing_family(c, c.execute('SELECT * FROM _bod_family WHERE singleton=1').fetchone())
            if row['id'] != data['family']:
                raise ValueError('Family mismatch')
            if not row.get('issuer'):
                return None  # Owner controls only expose saved families.
            if data['operation'] == 'access':
                action, reason = data['action'], data['reason'].strip()
                if action not in ('grant', 'revoke') or not reason or len(reason) > 300:
                    raise ValueError('Invalid access change')
                c.execute(AUDIT)
                value = int(action == 'grant')
                c.execute('UPDATE _bod_family SET free_access=? WHERE singleton=1', (value,))
                c.execute('''INSERT INTO _bod_access_audit
                    (family,action,reason,operator,created,previous,current) VALUES (?,?,?,?,?,?,?)''',
                    (row['id'], action, reason, 'oidc:' + ':'.join(actor), time.time(), row['free_access'], value))
            elif data['operation'] != 'show':
                raise ValueError('Unknown owner operation')
            row = self.billing_family(c, c.execute('SELECT * FROM _bod_family WHERE singleton=1').fetchone())
            # Strict projection: no identity, capabilities, customer IDs or bank data.
            result = {k: row[k] for k in ('id', 'trial_started', 'trial_until', 'paid_until', 'free_access')}
            result.update(is_guest=not bool(row.get('issuer')), billing_reconciled_at=None,
                          changed_at=None, audit=[])
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='_bod_billing'").fetchone():
                result['billing_reconciled_at'] = c.execute('SELECT reconciled FROM _bod_billing WHERE singleton=1').fetchone()['reconciled']
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='_bod_access_audit'").fetchone():
                result['audit'] = c.execute('''SELECT action,reason,created,previous,current
                    FROM _bod_access_audit ORDER BY id DESC LIMIT 20''').fetchall()
                if result['audit']:
                    result['changed_at'] = result['audit'][0]['created']
            return result
        result = self.transact(execute)
        if result and data['operation'] == 'access':
            # As in billing, a post-commit durable alarm publishes and rearms the
            # remaining paid/trial/session deadline after removing an override.
            await self.ctx.storage.setAlarm(int(time.time() * 1000) + 1)
        return json.dumps(result)


class OwnerDirectory:
    def register_family(self, family):
        if not re.fullmatch('[0-9a-f]{32}', family):
            raise ValueError('Invalid family')
        self.transact(lambda c: c.execute(REGISTRY))
        self.transact(lambda c: c.execute('INSERT OR IGNORE INTO owner_families VALUES (?)', (family,)))
        return True

    async def owner_http(self, request):
        try:
            allowed = owners(self.env)
            if not allowed:
                return response('Not found', 404)
            self.setup()
            principal = await self.inspect(cookie(request, COOKIE))
            if not principal:
                return response('Sign in to owner controls. <a href="/auth/login">Sign in with Google</a>', 401)
            actor = (principal.get('issuer'), principal.get('subject'))
            if principal['is_guest'] or actor not in allowed:
                return response('Owner access denied', 403)
            async def call(family, operation='show', **values):
                return json.loads(await self.family(family).owner(json.dumps(dict(
                    family=family, operation=operation, issuer=actor[0], subject=actor[1], **values))))
            path = urlsplit(request.url).path
            if path == '/owner/access':
                form = await self.form(request, principal)
                if form is None:
                    return response('Request denied', 403)
                family, action, reason = (form.get(k, '') for k in ('family', 'action', 'reason'))
                if (not re.fullmatch('[0-9a-f]{32}', family) or action not in ('grant', 'revoke')
                        or not reason.strip() or len(reason.strip()) > 300
                        or (action == 'revoke' and form.get('confirm_revoke') != 'yes')):
                    return response('Use an exact Family ID, valid action and short audit reason. Confirm revocation.', 400)
                saved = self.transact(lambda c: c.execute(
                    'SELECT 1 FROM identities WHERE family=? LIMIT 1', (family,)).fetchone())
                if not saved or await call(family, 'access', action=action, reason=reason) is None:
                    return response('Family not found', 404)
                # Read the exact target again after the committed RPC.
                if await call(family) is None:
                    raise ValueError('Access readback failed')
                return response(status=303, location='/owner?' + urlencode(dict(family=family, saved='1')))
            if path != '/owner':
                return response('Not found', 404)
            if request.method != 'GET':
                return response('Method not allowed', 405)
            query = parse_qs(urlsplit(request.url).query, keep_blank_values=True)
            if any(len(v) != 1 for v in query.values()):
                return response('Invalid query', 400)
            search = query.get('family', [''])[0]
            if search and not re.fullmatch('[0-9a-f]{1,32}', search):
                return response('Use 1–32 lowercase hexadecimal characters from the Family ID.', 400)
            try:
                page = int(query.get('page', ['1'])[0])
                if not 1 <= page <= 1000000:
                    raise ValueError()
            except ValueError:
                return response('Invalid page', 400)
            # Identity association is the authority for saved families. Filter
            # and deduplicate before pagination or any family RPC. Hex range
            # predicates use the existing family index (unlike default LIKE).
            ids = self.transact(lambda c: c.execute('''SELECT DISTINCT family FROM identities
                WHERE family >= ? AND family < ? ORDER BY family LIMIT 51 OFFSET ?''',
                (search, search + 'g', (page - 1) * 50)).fetchall())
            families = []
            # Bound in-flight DO requests while avoiding 50 serial network waits.
            for start in range(0, min(len(ids), 50), 5):
                rows = await asyncio.gather(*(call(item['family']) for item in ids[start:min(start + 5, 50)]))
                for row in rows:
                    if row and not row['is_guest']:
                        row['status'] = access_status(row)
                        families.append(row)
            html = Environment(loader=DictLoader(TEMPLATES), autoescape=True).get_template('owner.html').render(
                families=families, query=search, page=page, has_more=len(ids) > 50, saved_only=True,
                date_label=date_label, updated=date_label(time.time()), saved=query.get('saved') == ['1'],
                session={'csrf': principal['csrf']},
                url_for=lambda endpoint, **params: '/owner?' + urlencode(params))
            return response(html)
        except Exception as error:
            print('Owner request unavailable:', type(error).__name__)
            return response('Owner controls unavailable. No successful change is confirmed; reload the family before retrying.', 503)
