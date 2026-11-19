"""Google OIDC and durable browser/session directory for the Cloudflare target.

Only the Worker can call this DO. Provider I/O is always outside transactionSync.
Identity keys are canonical issuer + subject, never email or client-provided IDs.
"""
import hashlib
import hmac
import json
import secrets
import time
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit

import httpx
import js
from jinja2 import Environment, DictLoader
from workers import fetch

from cloudflare_templates import TEMPLATES
from cloudflare_security import cookie_header, ingress_status, runtime_mode

COOKIE = 'bod-local-session'
BROWSER = 'bod-oauth-browser'
CHOICE = 'bod-bank-choice'
GOOGLE = 'https://accounts.google.com'
SESSION_SECONDS = 30 * 86400
SCHEMA = '''
CREATE TABLE IF NOT EXISTS identities (
 issuer TEXT NOT NULL, subject TEXT NOT NULL, family TEXT NOT NULL UNIQUE,
 claim_hash TEXT, PRIMARY KEY(issuer,subject)
);
CREATE TABLE IF NOT EXISTS login_states (
 state TEXT PRIMARY KEY, browser TEXT NOT NULL, expires REAL NOT NULL,
 nonce TEXT NOT NULL, verifier TEXT NOT NULL, family TEXT, guest_hash TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
 hash TEXT PRIMARY KEY, family TEXT NOT NULL, csrf TEXT NOT NULL,
 expires REAL NOT NULL, device_token TEXT
);
CREATE TABLE IF NOT EXISTS device_returns (
 hash TEXT PRIMARY KEY, csrf TEXT NOT NULL, device_token TEXT NOT NULL, expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS choices (
 hash TEXT PRIMARY KEY, guest_hash TEXT NOT NULL, family TEXT NOT NULL,
 expires REAL NOT NULL, csrf TEXT NOT NULL, device_token TEXT, signed_token TEXT
);
'''


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def cookie(request, name):
    jar = SimpleCookie()
    try:
        jar.load(request.headers.get('Cookie', ''))
        return jar[name].value if name in jar else ''
    except Exception:
        return ''


def response(text='', status=200, location=None, cookies=(), env=None):
    headers = js.Headers.new()
    for key, value in {'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store',
                       'Referrer-Policy': 'same-origin', 'X-Content-Type-Options': 'nosniff',
                       'X-Frame-Options': 'DENY'}.items():
        headers.set(key, value)
    if location:
        headers.set('Location', location)
    for name, value, age in cookies:
        if env is None:
            raise ValueError('Cookie responses require validated runtime configuration')
        headers.append('Set-Cookie', cookie_header(env, name, value, age))
    return js.Response.new(text, status=status, headers=headers)


class WorkerTransport(httpx.AsyncBaseTransport):
    """Authlib handles OAuth; Workers fetch handles asynchronous bounded HTTP."""
    async def handle_async_request(self, request):
        result = await fetch(str(request.url), method=request.method,
                             headers=dict(request.headers), body=(await request.aread()) or None, redirect='manual',
                             signal=js.AbortSignal.timeout(15000))
        # Fixed endpoint host + status only: never log query strings or tokens.
        print('OIDC HTTP:', request.url.host, result.status)
        if 300 <= result.status < 400:
            raise ValueError('Provider redirect refused')
        raw = await result.bytes()
        if len(raw) > 256 * 1024:
            raise ValueError('Oversized provider response')
        return httpx.Response(result.status, content=raw, request=request)


class AuthDirectory:
    def response(self, *args, **kwargs):
        return response(*args, env=self.env, **kwargs)

    def setup(self):
        self.transact(lambda c: c.executescript(SCHEMA))

    def family(self, family):
        return self.env.FAMILIES.get(self.env.FAMILIES.idFromName(family))

    async def rpc(self, family_id, operation, **values):
        # Explicit JSON avoids Python RPC proxy/dropped-null ambiguity.
        return json.loads(await self.family(family_id).auth(json.dumps(dict(operation=operation, **values))))

    async def inspect(self, token):
        import re
        if not re.fullmatch(r'[0-9a-f]{32}\.[0-9a-f]{64}', token):
            return None
        return await self.rpc(token.split('.')[0], 'inspect', token=token)

    def config(self):
        client = str(getattr(self.env, 'BOD_OIDC_CLIENT_ID', ''))
        secret = str(getattr(self.env, 'BOD_OIDC_CLIENT_SECRET', ''))
        if not client or not secret:
            raise ValueError('Google client not configured')
        return client, secret

    async def metadata(self):
        # The fixture is a separate loopback HTTP service, enabled only by a local
        # test-only binding. Public issuer/endpoints are not browser-configurable.
        fixture = str(getattr(self.env, 'BOD_TEST_OIDC_ORIGIN', ''))
        if fixture:
            parts = urlsplit(fixture)
            if (str(self.env.BOD_LOCAL_ONLY) != 'true' or parts.scheme != 'http'
                    or parts.hostname != '127.0.0.1'):
                raise ValueError('Invalid local fixture')
            return dict(issuer=GOOGLE, authorization_endpoint=fixture + '/authorize',
                        token_endpoint=fixture + '/token', jwks_uri=fixture + '/jwks')
        async with httpx.AsyncClient(transport=WorkerTransport()) as client:
            r = await client.get(GOOGLE + '/.well-known/openid-configuration')
            r.raise_for_status()
            data = r.json()
        # Fixed provider trust roots, not arbitrary discovery SSRF/issuer switching.
        if (data['issuer'] != GOOGLE
                or data['authorization_endpoint'] != 'https://accounts.google.com/o/oauth2/v2/auth'
                or data['token_endpoint'] != 'https://oauth2.googleapis.com/token'
                or data['jwks_uri'] != 'https://www.googleapis.com/oauth2/v3/certs'):
            raise ValueError('Unexpected Google metadata')
        return data

    async def login(self, request):
        # Cryptography initializes entropy; import inside the request, not snapshot startup.
        from authlib.integrations.httpx_client import AsyncOAuth2Client
        client_id, _ = self.config()
        metadata = await self.metadata()
        token = cookie(request, COOKIE)
        principal = await self.inspect(token)
        browser = cookie(request, BROWSER) or secrets.token_hex(32)
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        now = time.time()
        def save(c):
            c.execute('DELETE FROM login_states WHERE expires<=?', (now,))
            c.execute('DELETE FROM choices WHERE expires<=?', (now,))
            c.execute('DELETE FROM device_returns WHERE expires<=?', (now,))
            c.execute('DELETE FROM sessions WHERE expires<=?', (now,))
            c.execute('INSERT INTO login_states VALUES (?,?,?,?,?,?,?)',
                      (digest(state), digest(browser), now + 600, nonce, verifier,
                       principal['id'] if principal and principal['is_guest'] else None,
                       digest(token.split('.')[1]) if principal and principal['is_guest'] else None))
        self.transact(save)
        async with AsyncOAuth2Client(client_id, scope='openid profile',
                                     redirect_uri=str(self.env.BOD_PUBLIC_ORIGIN) + '/auth/callback',
                                     code_challenge_method='S256') as oauth:
            url, _ = oauth.create_authorization_url(metadata['authorization_endpoint'], state=state,
                nonce=nonce, code_verifier=verifier, prompt='select_account')
        return self.response(status=302, location=url, cookies=[(BROWSER, browser, 600)])

    async def verify(self, code, pending):
        from authlib.integrations.httpx_client import AsyncOAuth2Client
        from authlib.jose import JsonWebToken
        client_id, secret = self.config()
        metadata = await self.metadata()
        async with AsyncOAuth2Client(client_id, secret, transport=WorkerTransport(),
                token_endpoint_auth_method='client_secret_post',
                redirect_uri=str(self.env.BOD_PUBLIC_ORIGIN) + '/auth/callback') as oauth:
            token = await oauth.fetch_token(metadata['token_endpoint'], code=code,
                                           code_verifier=pending['verifier'], grant_type='authorization_code')
        async with httpx.AsyncClient(transport=WorkerTransport()) as client:
            r = await client.get(metadata['jwks_uri'])
            r.raise_for_status()
            keys = r.json()
        # No long-lived JWKS cache: each login sees current trusted keys, including
        # rotation. Only RS256 is permitted; Authlib verifies before claim checks.
        claims = JsonWebToken(['RS256']).decode(token['id_token'], keys, claims_options={
            'iss': {'essential': True, 'values': [GOOGLE, 'accounts.google.com']},
            'aud': {'essential': True, 'value': client_id},
            'sub': {'essential': True}, 'iat': {'essential': True}, 'exp': {'essential': True}})
        claims.validate(leeway=30)
        now = time.time()
        if (not isinstance(claims['sub'], str) or not claims['sub'] or len(claims['sub']) > 255
                or claims.get('azp', client_id) != client_id
                or (isinstance(claims['aud'], list) and len(claims['aud']) > 1 and claims.get('azp') != client_id)
                or not isinstance(claims.get('nonce'), str)
                or not hmac.compare_digest(claims['nonce'], pending['nonce'])
                or not now - 600 <= claims['iat'] <= now + 30):
            raise ValueError('Invalid identity')
        return claims['sub']

    async def issue(self, family, device_token=None):
        token = family + '.' + secrets.token_hex(32)
        hashed = digest(token.split('.')[1])
        csrf, expires = secrets.token_hex(32), time.time() + SESSION_SECONDS
        # Install the family-local authorization before exposing a cookie. A
        # crash can leave only an unexposed random session, never unauthorized access.
        await self.rpc(family, 'session', hash=hashed, csrf=csrf, expires=expires,
                       device_bank=bool(device_token))
        self.transact(lambda c: c.execute('INSERT INTO sessions VALUES (?,?,?,?,?)',
                      (hashed, family, csrf, expires, device_token)))
        return token

    def forget(self, token):
        self.setup()
        parts = token.split('.')
        if len(parts) == 2:
            self.transact(lambda c: c.execute('DELETE FROM sessions WHERE hash=? AND family=?',
                                             (digest(parts[1]), parts[0])))

    async def revoke(self, token):
        principal = await self.inspect(token)
        if principal and not principal['is_guest']:
            hashed = digest(token.split('.')[1])
            await self.rpc(principal['id'], 'revoke', hash=hashed)
            self.transact(lambda c: c.execute('DELETE FROM sessions WHERE hash=?', (hashed,)))

    async def callback(self, request):
        query = parse_qs(urlsplit(request.url).query, keep_blank_values=True)
        if (len(query.get('state', [])) != 1 or not query['state'][0]
                or any(len(v) != 1 for v in query.values())
                or ('error' not in query and (len(query.get('code', [])) != 1 or not query['code'][0]))):
            return self.response('Sign-in failed. Please try again.', 400)
        pending = self.transact(lambda c: c.execute('''DELETE FROM login_states
            WHERE state=? AND browser=? AND expires>? RETURNING *''',
            (digest(query['state'][0]), digest(cookie(request, BROWSER)), time.time())).fetchone())
        if not pending or 'error' in query:
            return self.response('Sign-in failed. Please try again.', 400)
        try:
            subject = await self.verify(query['code'][0], pending)
        except Exception:
            return self.response('Sign-in failed. Please try again.', 400)
        token = cookie(request, COOKIE)
        principal = await self.inspect(token)
        if pending['family'] and (not principal or not principal['is_guest']
                or principal['id'] != pending['family']
                or not hmac.compare_digest(digest(token.split('.')[1]), pending['guest_hash'])):
            return self.response('This device bank is no longer available to save. Nothing was imported. '
                            '<a href="/auth/login">Sign in again</a>.', 409)
        def reserve(c):
            existing = c.execute('SELECT * FROM identities WHERE issuer=? AND subject=?', (GOOGLE, subject)).fetchone()
            if existing:
                return existing
            family = pending['family'] or secrets.token_hex(16)
            # The transaction serializes competing issuer/subject claims.
            if c.execute('SELECT 1 FROM identities WHERE family=?', (family,)).fetchone():
                return None
            c.execute('INSERT INTO identities VALUES (?,?,?,?)', (GOOGLE, subject, family, pending['guest_hash']))
            return dict(family=family, claim_hash=pending['guest_hash'])
        identity = self.transact(reserve)
        if not identity:
            return self.response('This device bank has already been saved. Nothing was imported.', 409)
        family = identity['family']
        # Durable reservation precedes idempotent family transition. A restart
        # between them is recovered by the next verified login for this identity.
        applied = await self.rpc(family, 'claim', family=family, issuer=GOOGLE, subject=subject,
                                 guest_hash=identity['claim_hash'])
        if not applied:
            # Failed reservations must not strand an identity after a concurrent
            # logout/capability rotation. Never remove a newer reservation.
            self.transact(lambda c: c.execute('DELETE FROM identities WHERE issuer=? AND subject=? AND family=? AND claim_hash IS ?',
                          (GOOGLE, subject, family, identity['claim_hash'])))
            return self.response('This device bank is no longer available to save. Nothing was imported.', 409)
        if pending['family'] and family != pending['family']:
            choice = secrets.token_hex(32)
            self.transact(lambda c: c.execute('INSERT INTO choices VALUES (?,?,?,?,?,?,?)',
                          (digest(choice), digest(token), family, time.time() + 600, principal['csrf'], None, None)))
            return self.response(status=302, location='/auth/choose-bank', cookies=[(CHOICE, choice, 600)])
        device = None
        if principal and not principal['is_guest']:
            saved = self.transact(lambda c: c.execute('SELECT device_token FROM sessions WHERE hash=?',
                                  (digest(token.split('.')[1]),)).fetchone())
            device = saved['device_token'] if saved else None
        new_token = await self.issue(family, device)
        await self.revoke(token)
        return self.response(status=302, location='/', cookies=[(COOKIE, new_token, SESSION_SECONDS),
                                                         (BROWSER, '', 0), (CHOICE, '', 0)])

    async def form(self, request, principal):
        if (request.method != 'POST' or request.headers.get('Origin') != str(self.env.BOD_PUBLIC_ORIGIN)
                or int(request.headers.get('Content-Length', '0')) > 8192):
            return None
        raw = await request.text()
        if len(raw) > 8192:
            return None
        form = parse_qs(raw, keep_blank_values=True)
        if (any(len(v) != 1 for v in form.values())
                or not hmac.compare_digest(form.get('csrf_token', [''])[0], principal['csrf'])):
            return None
        return {k: v[0] for k, v in form.items()}

    async def choose(self, request):
        token = cookie(request, COOKIE)
        principal = await self.inspect(token)
        choice = self.transact(lambda c: c.execute('SELECT * FROM choices WHERE hash=? AND guest_hash=? AND expires>?',
                    (digest(cookie(request, CHOICE)), digest(token), time.time())).fetchone())
        if not choice:
            return self.response(status=302, location='/')
        if choice['signed_token']:
            # Recover an explicitly approved switch if its response/process was
            # lost after rotating the device capability. Both browser secrets
            # must still match; an old guest token alone cannot obtain identity.
            return await self.finish_choice(token, choice)
        if not principal or not principal['is_guest']:
            return self.response('Device bank unavailable', 409)
        if request.method == 'GET':
            html = Environment(loader=DictLoader(TEMPLATES), autoescape=True).get_template('choose_bank.html').render(
                session={'csrf': principal['csrf']})
            return self.response(html)
        form = await self.form(request, principal)
        if not form or form.get('choice') not in ('existing', 'guest'):
            return self.response('Request denied', 403)
        key = digest(cookie(request, CHOICE))
        if form['choice'] == 'guest':
            consumed = self.transact(lambda c: c.execute('DELETE FROM choices WHERE hash=? AND signed_token IS NULL RETURNING *',
                                  (key,)).fetchone())
            return self.response(status=302 if consumed else 409, location='/' if consumed else None,
                            cookies=[(CHOICE, '', 0)])
        # Save a recoverable switch intent before changing the guest DO. A crash
        # or concurrent double click must not strand the preserved anonymous bank.
        device = principal['id'] + '.' + secrets.token_hex(32)
        signed = await self.issue(choice['family'], device)
        prepared = self.transact(lambda c: c.execute('''UPDATE choices SET device_token=?,signed_token=?
            WHERE hash=? AND signed_token IS NULL RETURNING *''', (device, signed, key)).fetchone())
        if not prepared:
            await self.revoke(signed)
            prepared = self.transact(lambda c: c.execute('SELECT * FROM choices WHERE hash=?', (key,)).fetchone())
        if not prepared or not prepared['signed_token']:
            return self.response('Choice expired', 409)
        return await self.finish_choice(token, prepared)

    async def finish_choice(self, token, choice):
        device, signed = choice['device_token'], choice['signed_token']
        if not await self.inspect(signed):
            return self.response('Sign in again to open your saved bank.', 409)
        rotated = await self.rpc(token.split('.')[0], 'rotate_guest', old=digest(token.split('.')[1]),
                                 new=digest(device.split('.')[1]))
        if not rotated:
            return self.response('Device bank unavailable', 409)
        return self.response(status=302, location='/', cookies=[(COOKIE, signed, SESSION_SECONDS), (CHOICE, '', 0)])

    async def device(self, request):
        token = cookie(request, COOKIE)
        principal = await self.inspect(token)
        # A committed explicit return can be retried after a lost cookie response;
        # the revoked signed session still cannot read the saved family bank.
        recovery = self.transact(lambda c: c.execute('SELECT * FROM device_returns WHERE hash=? AND expires>?',
                        (digest(token), time.time())).fetchone())
        if recovery:
            if await self.form(request, recovery) is None:
                return self.response('Request denied', 403)
            device = recovery['device_token']
        else:
            if not principal or principal['is_guest'] or await self.form(request, principal) is None:
                return self.response('Request denied', 403)
            saved = self.transact(lambda c: c.execute('SELECT device_token FROM sessions WHERE hash=? AND expires>?',
                        (digest(token.split('.')[1]), time.time())).fetchone())
            device = saved['device_token'] if saved else None
            if not device or not await self.inspect(device):
                return self.response('Device bank unavailable', 409)
            self.transact(lambda c: c.execute('INSERT INTO device_returns VALUES (?,?,?,?)',
                          (digest(token), principal['csrf'], device, time.time() + 600)))
        if not await self.inspect(device):
            return self.response('Device bank unavailable', 409)
        # Idempotent even if family revocation committed before a process failure.
        await self.rpc(token.split('.')[0], 'revoke', hash=digest(token.split('.')[1]))
        self.forget(token)
        return self.response(status=302, location='/', cookies=[(COOKIE, device, 34560000)])

    async def fetch(self, request):
        if status := ingress_status(self.env, request):
            return self.response('Request denied', status)
        path = urlsplit(request.url).path
        if path.startswith('/owner'):
            return await self.owner_http(request)
        if path == '/billing/webhook':
            return await self.stripe_webhook(request)
        try:
            self.setup()
            if path == '/auth/login':
                if request.method != 'GET':
                    return self.response('Method not allowed', 405)
                return await self.login(request)
            if path == '/auth/callback':
                if request.method != 'GET':
                    return self.response('Method not allowed', 405)
                return await self.callback(request)
            if path == '/auth/choose-bank':
                return await self.choose(request)
            if path == '/auth/device-bank':
                return await self.device(request)
        except Exception as error:
            # Log only exception class, never provider tokens, claims or URLs.
            print('Sign-in unavailable:', type(error).__name__,
                  error.name if isinstance(error, ModuleNotFoundError) else '')
            return self.response('Sign-in is temporarily unavailable. Your bank was not merged or deleted. '
                            'Try signing in again. <a href="/">Back to your bank</a>', 503)
        return self.response('Not found', 404)
