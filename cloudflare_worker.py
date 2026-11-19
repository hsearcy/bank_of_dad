"""Current Bank of Dad on Workers + one SQLite Durable Object per family.

Guest bank and Google identity/claiming are integrated. Public HTTPS supports
an explicit no-payments pilot or pinned live billing; provider acceptance is separate.
"""
import os
os.environ['BANK_OF_DAD_MODE'] = 'cloudflare'

import hashlib
import hmac
import json
import math
import re
import secrets
import time
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

import js
from jinja2 import DictLoader
from pyodide.ffi import create_proxy, to_js
from workers import DurableObject, Response, WorkerEntrypoint, wsgi

import db
import bank
from app import app
from cloudflare_sql import Connection
from cloudflare_templates import TEMPLATES
from guest_bank import create_sample
from hosted_access import TRIAL_SECONDS, access_status
from cloudflare_billing import FamilyBilling, BillingDirectory
from cloudflare_owner import FamilyOwner, OwnerDirectory
from cloudflare_security import cookie_header, ingress_status, runtime_mode

app.jinja_loader = DictLoader(TEMPLATES)
COOKIE = 'bod-local-session'
TOKEN = re.compile(r'[0-9a-f]{32}\.[0-9a-f]{64}')
CREATE_HEADER = 'X-Bod-Create'
META_SCHEMA = '''CREATE TABLE _bod_family (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL CHECK(version=1),
    id TEXT NOT NULL, guest_hash TEXT NOT NULL, csrf TEXT NOT NULL,
    trial_started REAL NOT NULL, trial_until REAL NOT NULL,
    trial_policy INTEGER NOT NULL DEFAULT 0,
    paid_until REAL NOT NULL DEFAULT 0, free_access INTEGER NOT NULL DEFAULT 0,
    welcome_dismissed INTEGER NOT NULL DEFAULT 0, customer TEXT,
    revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1))
)'''


def token_from(request):
    cookie = SimpleCookie()
    try:
        cookie.load(request.headers.get('Cookie', ''))
        value = cookie[COOKIE].value if COOKIE in cookie else ''
    except Exception:
        return ''
    return value if TOKEN.fullmatch(value) else ''


def denied(status=401):
    return Response(json.dumps({'error': 'Device bank unavailable' if status == 401 else 'Request denied'}),
                    status=status, headers={'Content-Type': 'application/json', 'Cache-Control': 'no-store'})


class FamilyBank(FamilyOwner, FamilyBilling, DurableObject):
    def transact(self, operation):
        conn = Connection(self.ctx.storage.sql)
        result = None
        def run():
            nonlocal result
            conn.in_transaction = True
            try:
                result = operation(conn)
                if conn.failed:
                    raise RuntimeError('Failed SQL transaction')
            finally:
                conn.in_transaction = False
        proxy = create_proxy(run)
        try:
            self.ctx.storage.transactionSync(proxy)
        finally:
            proxy.destroy()
        return result

    def authenticate(self, conn, token, create=False):
        objects = conn.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND name NOT LIKE '_cf_%'").fetchall()
        if not objects:
            if not create:
                return None
            conn.executescript(db.SCHEMA)
            conn.execute(META_SCHEMA)
            family, capability = token.split('.')
            now = time.time()
            conn.execute('''INSERT INTO _bod_family
                (singleton,version,id,guest_hash,csrf,trial_started,trial_until,trial_policy)
                VALUES (1,1,?,?,?,?,?,1)''',
                (family, hashlib.sha256(capability.encode()).hexdigest(), secrets.token_hex(32), now, now + TRIAL_SECONDS))
            create_sample(conn)
        elif not any(row['name'] == '_bod_family' for row in objects):
            raise RuntimeError('Unversioned Cloudflare bank; explicit migration required')
        db._migrate(conn)
        rows = conn.execute('SELECT * FROM _bod_family').fetchall()
        if len(rows) != 1 or rows[0]['version'] != 1:
            raise RuntimeError('Unsupported Cloudflare bank schema')
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(_bod_family)')}
        if 'issuer' not in columns:
            conn.execute('ALTER TABLE _bod_family ADD COLUMN issuer TEXT')
            conn.execute('ALTER TABLE _bod_family ADD COLUMN subject TEXT')
        conn.execute('''CREATE TABLE IF NOT EXISTS _bod_sessions (
            hash TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL,
            device_bank INTEGER NOT NULL DEFAULT 0)''')
        family = self.billing_family(conn, conn.execute('SELECT * FROM _bod_family WHERE singleton=1').fetchone())
        name, capability = token.split('.')
        hashed = hashlib.sha256(capability.encode()).hexdigest()
        if family['id'] != name:
            return None
        if not family['issuer'] and not family['revoked'] and hmac.compare_digest(family['guest_hash'], hashed):
            return dict(family, is_guest=True)
        saved = conn.execute('SELECT * FROM _bod_sessions WHERE hash=? AND expires>?', (hashed, time.time())).fetchone()
        if not family['issuer'] or not saved:
            return None
        return dict(family, csrf=saved['csrf'], is_guest=False, device_bank=bool(saved['device_bank']))

    def auth(self, payload):
        """Binding-only RPC. Never routed from a client-controlled HTTP path."""
        data = json.loads(payload)
        operation = data['operation']
        def execute(c):
            if operation == 'inspect':
                family = self.authenticate(c, data['token'])
                return ({k: family[k] for k in ('id', 'csrf', 'is_guest', 'issuer', 'subject')} if family else None)
            if operation == 'claim':
                objects = c.execute("SELECT 1 FROM sqlite_master WHERE name='_bod_family'").fetchone()
                if not objects:
                    if data['guest_hash'] is not None:
                        return False
                    # Directory-created family: start the trial once in this DO.
                    self.authenticate(c, data['family'] + '.' + secrets.token_hex(32), create=True)
                family = c.execute('SELECT * FROM _bod_family WHERE singleton=1').fetchone()
                # Ensure schema migration even when recovering an earlier reservation.
                self.authenticate(c, family['id'] + '.' + '0' * 64)
                family = c.execute('SELECT * FROM _bod_family WHERE singleton=1').fetchone()
                if family['issuer']:
                    return family['issuer'] == data['issuer'] and family['subject'] == data['subject']
                if family['revoked'] or (data['guest_hash'] is not None and not hmac.compare_digest(family['guest_hash'], data['guest_hash'])):
                    return False
                c.execute('UPDATE _bod_family SET issuer=?,subject=?,guest_hash=\'\',csrf=? WHERE singleton=1',
                          (data['issuer'], data['subject'], secrets.token_hex(32)))
                c.execute('DELETE FROM _bod_sessions')
                return True
            if operation == 'session':
                family = c.execute('SELECT * FROM _bod_family WHERE singleton=1').fetchone()
                if not family or not family['issuer']:
                    raise ValueError('Unclaimed family')
                c.execute('DELETE FROM _bod_sessions WHERE expires<=?', (time.time(),))
                c.execute('INSERT INTO _bod_sessions VALUES (?,?,?,?)',
                          (data['hash'], data['csrf'], data['expires'], int(data['device_bank'])))
                return True
            if operation == 'revoke':
                c.execute('DELETE FROM _bod_sessions WHERE hash=?', (data['hash'],))
                return True
            if operation == 'rotate_guest':
                row = c.execute('SELECT * FROM _bod_family WHERE singleton=1').fetchone()
                if row['issuer'] or row['revoked']:
                    return False
                if hmac.compare_digest(row['guest_hash'], data['new']):
                    return True  # idempotent recovery of a persisted switch intent
                if not hmac.compare_digest(row['guest_hash'], data['old']):
                    return False
                c.execute('UPDATE _bod_family SET guest_hash=?,csrf=? WHERE singleton=1', (data['new'], secrets.token_hex(32)))
                return True
            raise ValueError('Unknown auth operation')
        result = self.transact(execute)
        if operation in ('claim', 'revoke', 'rotate_guest'):
            self.publish()
        return json.dumps(result)

    async def fetch(self, request):
        if status := ingress_status(self.env, request):
            return denied(status)
        token = token_from(request)
        if not token:
            return denied()
        path = urlsplit(request.url).path
        if path == '/api/updates':
            if request.headers.get('Origin') != str(self.env.BOD_PUBLIC_ORIGIN):
                return denied(403)
            if request.headers.get('Upgrade', '').lower() != 'websocket':
                return denied(426)
            family = self.transact(lambda c: self.authenticate(c, token))
            if not family:
                return denied()
            if not access_status(family)['active']:
                return denied(402)
            pair = js.WebSocketPair.new()
            client, server = js.Object.values(pair)
            # Native hibernating sockets: no Python thread or permanently pinned
            # accept() loop. Attach only the capability hash, not bank contents.
            self.ctx.acceptWebSocket(server)
            server.serializeAttachment(to_js({'hash': hashlib.sha256(token.split('.')[1].encode()).hexdigest()}, dict_converter=js.Object.fromEntries))
            server.send('{"type":"connected"}')
            # One alarm at the real entitlement boundary, not a background poll.
            await self.arm_alarm()
            return js.Response.new(None, status=101, webSocket=client)

        # Fully buffer a bounded body BEFORE entering transactionSync. WSGI body
        # reads and template rendering below perform no network/async operations.
        body = bytearray()
        if request.body:
            async for chunk in request.body:
                body.extend(chunk.to_bytes())
                if len(body) > 64 * 1024:
                    return denied(413)
        if path in ('/billing/checkout', '/billing/portal', '/billing/refresh'):
            return await self.billing_http(request, token, bytes(body))
        environ = wsgi.build_environ(request, self.env, bytes(body))
        result = {}
        def dispatch(conn):
            create = request.headers.get(CREATE_HEADER) == '1' and path == '/' and request.method == 'GET'
            family = self.authenticate(conn, token, create=create)
            if family is None:
                return False
            environ.update({'bod.connection': conn, 'bod.family': family,
                            'bod.origin': str(self.env.BOD_PUBLIC_ORIGIN),
                            'bod.local_preview': runtime_mode(self.env) == 'local',
                            'bod.support_url': str(getattr(self.env, 'BOD_SUPPORT_URL', '')),
                            'bod.public_pilot': runtime_mode(self.env) == 'public' and str(getattr(self.env, 'BOD_BILLING_MODE', 'unavailable')) == 'unavailable'})
            def start_response(status, headers, exc_info=None):
                if exc_info:
                    raise exc_info[1]
                result.update(status=int(status.split()[0]), headers=headers)
            iterable = app.wsgi_app(environ, start_response)
            try:
                result['body'] = b''.join(iterable)
            finally:
                if hasattr(iterable, 'close'):
                    iterable.close()
            # Flask HTTP errors are ordinary outcomes; unexpected exceptions
            # propagate (PROPAGATE_EXCEPTIONS), rolling back the whole request.
            if result['status'] >= 500 and result['status'] != 503:
                raise RuntimeError('Failed application request')
            return True
        try:
            if not self.transact(dispatch):
                return denied()
        except Exception as error:
            # No tokens, SQL parameters or tracebacks in client responses/logs.
            print('Family request failed:', type(error).__name__)
            return denied(500)
        if environ.get('bod.changed') or environ.get('bod.logged_out'):
            try:
                self.publish()
            except Exception:
                # A failed notification must not turn a committed mutation into
                # an apparent failed write. The real UI reconciles every 30s.
                print('Bank update notification failed')
        await self.arm_alarm()
        headers = js.Headers.new()
        for key, value in result['headers']:
            headers.append(key, value)
        if environ.get('bod.logged_out'):
            headers.append('Set-Cookie', cookie_header(self.env, COOKIE, '', 0))
        payload = None if result['status'] in (204, 304) else to_js(result['body'])
        return js.Response.new(payload, status=result['status'], headers=headers)

    def publish(self):
        family = self.transact(lambda c: self.billing_family(c, c.execute('SELECT * FROM _bod_family WHERE singleton=1').fetchone()))
        for socket in self.ctx.getWebSockets():
            try:
                attachment = socket.deserializeAttachment()
                saved = self.transact(lambda c: c.execute('SELECT expires FROM _bod_sessions WHERE hash=? AND expires>?',
                    (str(attachment.hash), time.time())).fetchone())
                valid = (saved if family and family.get('issuer') else
                         family and not family['revoked'] and attachment.hash == family['guest_hash'])
                if not family or not valid or not access_status(family)['active']:
                    socket.close(1008, 'Access ended')
                else:
                    socket.send('{"type":"state_changed"}')
            except Exception:
                socket.close(1011, 'Reconnect')

    async def arm_alarm(self):
        family = self.transact(lambda c: self.billing_family(c, c.execute('SELECT * FROM _bod_family WHERE singleton=1').fetchone()))
        access = access_status(family)
        deadlines = [access['until']] if access['active'] and access['until'] else []
        def interest_deadline(c):
            db._migrate(c)
            if not bank.auto_interest_enabled(c):
                return None
            due = c.execute('SELECT MIN(auto_interest_due_ts) AS due FROM kids').fetchone()['due']
            offset = float(db.get_setting(c, "time_offset", "0") or 0)
            return max(time.time()+.01, due-offset) if due is not None else None
        due = self.transact(interest_deadline)
        if due is not None:
            deadlines.append(due)
        # Never let a later device's socket postpone an earlier session expiry.
        saved = self.transact(lambda c: c.execute('SELECT MIN(expires) AS expires FROM _bod_sessions WHERE expires>?',
                                                  (time.time(),)).fetchone())
        if saved and saved['expires']:
            deadlines.append(saved['expires'])
        if deadlines:
            scheduled = math.ceil(min(deadlines) * 1000)
            # Billing/owner writes may have queued an immediate invalidation.
            # Never postpone it, or churn the persisted alarm on dashboard reads.
            existing = await self.ctx.storage.getAlarm()
            if existing is None or scheduled < float(existing):
                await self.ctx.storage.setAlarm(scheduled)

    async def alarm(self):
        # One persisted alarm multiplexes financial, session and access deadlines.
        # Settlement and ledger are one transaction, even after an idle restart.
        def settle(c):
            db._migrate(c)
            bank.settle_auto_interest(c)
        self.transact(settle)
        self.publish()
        await self.arm_alarm()

    async def webSocketMessage(self, socket, message):
        # Clients send no commands over this content-free invalidation channel.
        socket.close(1008, 'Read-only updates')

    async def webSocketClose(self, socket, code, reason, was_clean):
        # A browser close() without a status arrives as reserved code 1005;
        # reserved/synthetic codes must never be sent on the wire.
        socket.close(1000 if code in (1005, 1006, 1015) else code, reason)

    async def webSocketError(self, socket, error):
        socket.close(1011, 'Reconnect')


from cloudflare_auth import AuthDirectory


class Directory(OwnerDirectory, BillingDirectory, AuthDirectory, FamilyBank):
    pass


class Default(WorkerEntrypoint):
    async def admission(self, request, path):
        if runtime_mode(self.env) == 'local':
            return 0
        # CF-Connecting-IP is supplied/overwritten by Cloudflare at public ingress;
        # X-Forwarded-For and client-generated cookies never define this budget.
        ip = request.headers.get('CF-Connecting-IP', '')
        if not ip:
            return 503
        create = path == '/' and request.method == 'GET' and not token_from(request)
        budgets = [('BOD_INGRESS_LIMIT', 'ip:' + ip)]
        if create:
            budgets += [('BOD_CREATE_LIMIT', 'ip:' + ip), ('BOD_CREATE_GLOBAL_LIMIT', 'new-family')]
        try:
            for name, key in budgets:
                limiter = getattr(self.env, name)
                result = await limiter.limit(to_js({'key': key}, dict_converter=js.Object.fromEntries))
                if not result.success:
                    return 429
        except Exception:
            # No unbounded fallback when a binding is absent or unavailable.
            return 503
        return 0

    async def fetch(self, request):
        if status := ingress_status(self.env, request):
            return denied(status)
        path = urlsplit(request.url).path
        if path.startswith('/static/') or path in ('/sw.js', '/robots.txt'):
            asset = '/hosted-sw.js' if path == '/sw.js' else ('/robots.txt' if path == '/robots.txt' else path[len('/static'):])
            url = str(self.env.BOD_PUBLIC_ORIGIN) + asset
            response = await self.env.ASSETS.fetch(js.Request.new(url, method=str(request.method)))
            return response
        if status := await self.admission(request, path):
            return denied(status)
        if path == '/auth/signed-out':
            return Response('<p>You are signed out.</p><a href="/">Start a new device bank</a>',
                            headers={'Content-Type': 'text/html', 'Cache-Control': 'no-store'})
        if path in ('/auth/login', '/auth/callback', '/auth/choose-bank', '/auth/device-bank', '/billing/webhook', '/owner', '/owner/access') or path.startswith('/owner/'):
            stub = self.env.DIRECTORY.get(self.env.DIRECTORY.idFromName('google-v1'))
            return await stub.fetch(request.js_object)
        token = token_from(request)
        has_device_cookie = any(part.strip().startswith(COOKIE + '=') for part in request.headers.get('Cookie', '').split(';'))
        create = not token and not has_device_cookie and path == '/' and request.method == 'GET'
        if not token and not create:
            return denied()
        if create:
            token = secrets.token_hex(16) + '.' + secrets.token_hex(32)
        headers = js.Headers.new(request.js_object.headers)
        # Client-supplied bootstrap flags can never reach an object.
        headers.delete(CREATE_HEADER)
        if create:
            headers.set(CREATE_HEADER, '1')
            headers.set('Cookie', f'{COOKIE}={token}')
        forwarded = js.Request.new(request.js_object, headers=headers)
        family = token.split('.')[0]
        stub = self.env.FAMILIES.get(self.env.FAMILIES.idFromName(family))
        try:
            response = await stub.fetch(forwarded)
        except Exception:
            return denied(500)
        if path == '/auth/logout' and response.status == 302:
            try:
                directory = self.env.DIRECTORY.get(self.env.DIRECTORY.idFromName('google-v1'))
                await directory.forget(token)
            except Exception:
                # Family-local revocation already committed; stale directory rows
                # cannot authorize and are bounded by the original expiry.
                print('Session directory cleanup deferred')
        if create and response.status == 200:
            try:
                directory = self.env.DIRECTORY.get(self.env.DIRECTORY.idFromName('google-v1'))
                await directory.register_family(family)
            except Exception:
                # Do not lose a new guest's only capability on registry failure.
                # Owner can still find this bank by its exact support Family ID.
                print('Guest directory registration deferred')
            raw = response.js_object if isinstance(response, Response) else response
            result = js.Response.new(raw.body, raw)
            result.headers.append('Set-Cookie', cookie_header(self.env, COOKIE, token, 34560000))
            return result
        return response
