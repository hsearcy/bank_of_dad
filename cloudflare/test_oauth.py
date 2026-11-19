"""Real local workerd/DO SQL + HTTP OIDC fixture + Chromium.

The provider below is deliberately synthetic, not Google acceptance. Signatures,
PKCE, OAuth HTTP, browser cookies, bank routes and revocations are real.
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / 'tests'))
from parent_pin_ui import open_parent

import base64
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from authlib.jose import JsonWebKey, JsonWebToken
from cryptography.hazmat.primitives.asymmetric import rsa

from test_runtime import Runtime, INIT


class Provider:
    def __init__(self):
        self.subject = 'disposable-parent-a'
        self.mode = 'ok'
        self.codes = {}
        self.calls = []
        self.token_entered = threading.Event()
        self.token_release = threading.Event()
        self.rotate()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def reply(self, data, status=200):
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())
            def do_GET(self):
                path = urlsplit(self.path)
                if path.path == '/jwks':
                    owner.calls.append('jwks')
                    return self.reply({'keys': [owner.jwk]})
                if path.path != '/authorize':
                    return self.reply({}, 404)
                q = {k: v[0] for k, v in parse_qs(path.query).items()}
                assert q['scope'] == 'openid profile'
                assert q['code_challenge_method'] == 'S256' and len(q['code_challenge']) == 43
                code = secrets.token_hex(16)
                owner.codes[code] = (q, owner.subject, owner.mode, owner.private, owner.kid)
                self.send_response(302)
                self.send_header('Location', q['redirect_uri'] + '?' + urlencode({'code': code, 'state': q['state']}))
                self.end_headers()
            def do_POST(self):
                owner.calls.append('token')
                q = {k: v[0] for k, v in parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode()).items()}
                saved = owner.codes.pop(q.get('code'), None)
                if not saved:
                    return self.reply({'error': 'invalid_grant'}, 400)
                original, subject, mode, private, kid = saved
                challenge = base64.urlsafe_b64encode(hashlib.sha256(q.get('code_verifier', '').encode()).digest()).rstrip(b'=').decode()
                if (q.get('client_id') != 'fixture-client' or q.get('client_secret') != 'fixture-secret'
                        or q.get('redirect_uri') != original['redirect_uri'] or q.get('grant_type') != 'authorization_code'
                        or challenge != original['code_challenge']):
                    return self.reply({'error': 'invalid_grant'}, 400)
                if mode == 'slow':
                    owner.token_entered.set()
                    assert owner.token_release.wait(15)
                if mode == 'provider_error':
                    return self.reply({'error': 'invalid_grant'}, 400)
                now = int(time.time())
                claims = dict(iss='https://accounts.google.com', sub=subject, aud='fixture-client',
                              nonce=original['nonce'], iat=now, exp=now + 300)
                changes = {'issuer': {'iss': 'https://evil.invalid'}, 'google_issuer': {'iss': 'accounts.google.com'},
                    'audience': {'aud': 'other-client'}, 'azp': {'azp': 'other-client'},
                    'nonce': {'nonce': 'wrong'}, 'nonce_disabled': {'nonce': 'wrong', 'nonce_supported': False},
                    'multi_missing_azp': {'aud': ['fixture-client', 'other']}, 'expired': {'exp': now - 60},
                    'old_iat': {'iat': now - 700}, 'future_iat': {'iat': now + 120},
                    'missing_sub': {'sub': ''}, 'multi_audience': {'aud': ['fixture-client', 'other'], 'azp': 'other'}}
                claims.update(changes.get(mode, {}))
                key = rsa.generate_private_key(public_exponent=65537, key_size=2048) if mode == 'signature' else private
                if mode == 'unknown_key':
                    kid = 'unknown-key'
                if mode == 'algorithm':
                    encoded = JsonWebToken(['HS256']).encode({'alg': 'HS256', 'kid': kid}, claims, b'x' * 32).decode()
                else:
                    encoded = JsonWebToken(['RS256']).encode({'alg': 'RS256', 'kid': kid}, claims, key).decode()
                self.reply({'token_type': 'Bearer', 'access_token': 'unused-fixture-token', 'id_token': encoded})
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.origin = f'http://127.0.0.1:{self.http.server_port}'
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def rotate(self):
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = secrets.token_hex(8)
        self.jwk = JsonWebKey.import_key(self.private.public_key(), {'kty': 'RSA', 'kid': self.kid, 'use': 'sig', 'alg': 'RS256'}).as_dict()

    def stop(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()


@pytest.fixture(scope='module')
def oauth(tmp_path_factory):
    provider = Provider()
    base = tmp_path_factory.mktemp('oauth-runtime')
    runtime = Runtime(base, {'BOD_OIDC_CLIENT_ID': 'fixture-client', 'BOD_OIDC_CLIENT_SECRET': 'fixture-secret',
                             'BOD_TEST_OIDC_ORIGIN': provider.origin})
    runtime.start()
    try:
        yield runtime, provider
    finally:
        runtime.stop()
        provider.stop()


def client(runtime):
    return httpx.Client(base_url=runtime.origin, timeout=45, follow_redirects=False)


def setup_parent(c, runtime):
    token = csrf(c)
    status = c.get('/api/admin/status')
    assert status.status_code == 200
    if not status.json()['pin_set']:
        assert c.post('/api/admin/pin', json={'new_pin': '1234'},
                      headers={'Origin': runtime.origin, 'X-CSRF-Token': token}).json()['ok']
    c.headers['X-Dad-Pin'] = '1234'


def start_login(c, provider):
    r = c.get('/auth/login')
    assert r.status_code == 302, r.text
    assert r.headers['location'].startswith(provider.origin + '/authorize?')
    r = c.get(r.headers['location'])
    assert r.status_code == 302
    return r.headers['location']


def login(c, provider, subject='disposable-parent-a', mode='ok'):
    provider.subject, provider.mode = subject, mode
    callback = start_login(c, provider)
    result = c.get(callback)
    assert result.status_code == 302, result.text
    return result


def csrf(c):
    r = c.get('/')
    assert r.status_code == 200
    return re.search('name="csrf-token" content="([0-9a-f]+)"', r.text)[1]


def bank(c):
    r = c.get('/api/state')
    assert r.status_code == 200, r.text
    result = r.json()
    result.pop('server_now', None)
    return result


def test_claim_real_bank_and_directory_persistence(oauth):
    runtime, provider = oauth
    with client(runtime) as c:
        old_csrf = csrf(c)
        setup_parent(c, runtime)
        old = c.cookies['bod-local-session']
        assert c.post('/api/admin/gift', json=dict(kid_id=1, amount_cents=321),
                      headers={'Origin': runtime.origin, 'X-CSRF-Token': old_csrf}).status_code == 200
        before = bank(c)
        path = runtime.database('bod-local-session=' + old)
        with sqlite3.connect(path) as sql:
            dates = sql.execute('SELECT trial_started,trial_until FROM _bod_family').fetchone()
        login(c, provider, 'claim-preserves-bank')
        new = c.cookies['bod-local-session']
        assert new != old and new.split('.')[0] == old.split('.')[0]
        assert bank(c) == before
        assert csrf(c) != old_csrf
        assert runtime.call('/api/state', cookie='bod-local-session=' + old)[0] == 401
        assert c.post('/api/admin/gift', json=dict(kid_id=1, amount_cents=1),
                      headers={'Origin': runtime.origin, 'X-CSRF-Token': old_csrf}).status_code == 403
        runtime.stop()
        with sqlite3.connect(path) as sql:
            assert sql.execute('SELECT trial_started,trial_until FROM _bod_family').fetchone() == dates
            assert sql.execute('SELECT issuer,subject,guest_hash FROM _bod_family').fetchone() == (
                'https://accounts.google.com', 'claim-preserves-bank', '')
        runtime.start()
        assert bank(c) == before
        # Direct public sign-in on a fresh device opens the saved bank, no new trial.
        with client(runtime) as other:
            login(other, provider, 'claim-preserves-bank')
            assert other.cookies['bod-local-session'].split('.')[0] == old.split('.')[0]
            assert bank(other) == before
            signed_csrf = csrf(c)
            assert c.post('/auth/logout', data={'csrf_token': signed_csrf}, headers={'Origin': runtime.origin}).status_code == 302
            assert runtime.call('/api/state', cookie='bod-local-session=' + new)[0] == 401
            assert bank(other) == before  # device-scoped logout
    assert 'token' in provider.calls and 'jwks' in provider.calls


@pytest.mark.parametrize('mode', ['issuer', 'audience', 'azp', 'nonce', 'expired', 'old_iat', 'future_iat',
                                  'missing_sub', 'signature', 'unknown_key', 'algorithm', 'multi_audience', 'multi_missing_azp',
                                  'nonce_disabled', 'provider_error'])
def test_signed_bad_tokens_fail_without_claim(oauth, mode):
    runtime, provider = oauth
    with client(runtime) as c:
        csrf(c)
        old = c.cookies['bod-local-session']
        provider.subject, provider.mode = 'never-linked-' + mode, mode
        callback = start_login(c, provider)
        calls = len(provider.calls)
        r = c.get(callback)
        assert r.status_code == 400, r.text
        assert provider.calls[calls] == 'token'
        if mode != 'provider_error':
            assert provider.calls[calls + 1] == 'jwks'  # reached the intended verifier boundary
        assert c.cookies['bod-local-session'] == old
        assert bank(c)['kids'][0]['wallet_cents'] == 500
        assert c.get(callback).status_code == 400  # even provider failures consume state


def test_state_browser_shape_pkce_replay_and_concurrent_callback(oauth):
    runtime, provider = oauth
    provider.subject, provider.mode = 'state-owner', 'ok'
    with client(runtime) as c, client(runtime) as other:
        csrf(c)
        callback = start_login(c, provider)
        count = provider.calls.count('token')
        assert other.get(callback).status_code == 400
        assert provider.calls.count('token') == count
        assert c.get(callback + '&state=duplicate').status_code == 400
        assert provider.calls.count('token') == count
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: c.get(callback).status_code, range(2)))
        assert sorted(results) == [302, 400]
        assert provider.calls.count('token') == count + 1
        assert c.get(callback).status_code == 400
        # Code substitution: another browser's code has a different PKCE verifier.
        provider.subject = 'substitution-victim'
        a = start_login(c, provider)
        b = start_login(other, provider)
        qa, qb = parse_qs(urlsplit(a).query), parse_qs(urlsplit(b).query)
        swapped = '/auth/callback?' + urlencode({'state': qa['state'][0], 'code': qb['code'][0]})
        assert c.get(swapped).status_code == 400
        assert c.get(a).status_code == 400
        # A provider error consumes the bound transaction without exchanging.
        bq = parse_qs(urlsplit(b).query)
        assert other.get('/auth/callback?' + urlencode({'state': bq['state'][0], 'error': 'access_denied'})).status_code == 400
        assert other.get(b).status_code == 400


def test_collision_explicit_choice_preserves_both_banks_and_return(oauth):
    runtime, provider = oauth
    with client(runtime) as owner, client(runtime) as guest:
        login(owner, provider, 'collision-owner')
        saved_family = owner.cookies['bod-local-session'].split('.')[0]
        token = csrf(guest)
        original = guest.cookies['bod-local-session']
        setup_parent(guest, runtime)
        guest.post('/api/admin/gift', json=dict(kid_id=1, amount_cents=123),
                   headers={'Origin': runtime.origin, 'X-CSRF-Token': token}).raise_for_status()
        result = login(guest, provider, 'collision-owner')
        assert result.headers['location'] == '/auth/choose-bank'
        assert guest.cookies['bod-local-session'] == original
        assert bank(guest)['kids'][0]['wallet_cents'] == 623
        assert 'Nothing has been merged' in guest.get('/auth/choose-bank').text
        assert guest.post('/auth/choose-bank', data={'choice': 'existing', 'csrf_token': token},
                          headers={'Origin': 'https://evil.invalid'}).status_code == 403
        r = guest.post('/auth/choose-bank', data={'choice': 'existing', 'csrf_token': token}, headers={'Origin': runtime.origin})
        assert r.status_code == 302, r.text
        assert guest.cookies['bod-local-session'].split('.')[0] == saved_family
        assert bank(guest)['kids'][0]['wallet_cents'] == 500
        assert runtime.call('/api/state', cookie='bod-local-session=' + original)[0] == 401
        assert 'Return to this device' in guest.get('/').text
        signed = guest.cookies['bod-local-session']
        r = guest.post('/auth/device-bank', data={'csrf_token': csrf(guest)}, headers={'Origin': runtime.origin})
        assert r.status_code == 302, r.text
        assert guest.cookies['bod-local-session'].split('.')[0] == original.split('.')[0]
        assert bank(guest)['kids'][0]['wallet_cents'] == 623
        assert runtime.call('/api/state', cookie='bod-local-session=' + signed)[0] == 401
        assert bank(owner)['kids'][0]['wallet_cents'] == 500
        # Keep guest is a separate explicit path and does not touch either ledger.
        token = csrf(guest)
        login(guest, provider, 'collision-owner')
        r = guest.post('/auth/choose-bank', data={'choice': 'guest', 'csrf_token': token}, headers={'Origin': runtime.origin})
        assert r.status_code == 302
        assert bank(guest)['kids'][0]['wallet_cents'] == 623
        login(guest, provider, 'different-parent-saves-trial')
        assert bank(guest)['kids'][0]['wallet_cents'] == 623


def test_google_issuer_alias_and_signing_key_rotation(oauth):
    runtime, provider = oauth
    with client(runtime) as c:
        login(c, provider, 'key-rotation-parent', 'google_issuer')
        saved = c.cookies['bod-local-session'].split('.')[0]
        provider.rotate()
        login(c, provider, 'key-rotation-parent')
        assert c.cookies['bod-local-session'].split('.')[0] == saved
        assert bank(c)['kids'][0]['wallet_cents'] == 500


def test_real_frontend_claim_collision_and_idle_socket_revocation(oauth):
    from playwright.sync_api import sync_playwright, expect
    runtime, provider = oauth
    provider.subject, provider.mode = 'browser-parent', 'ok'
    with sync_playwright() as p, p.chromium.launch() as browser:
        ctx = browser.new_context(viewport={'width': 390, 'height': 844})
        ctx.add_init_script(INIT)
        page = ctx.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(runtime.origin)
        expect(page.locator('#kids .big')).to_have_text('$7.00')
        old = ctx.cookies()
        old_cookie = next(x for x in old if x['name'] == 'bod-local-session')['value']
        stale = browser.new_context(storage_state=ctx.storage_state())
        stale.add_init_script(INIT)
        tab = stale.new_page()
        tab.goto(runtime.origin)
        tab.wait_for_function('window.__sockets.some(s => s.readyState === 1)')
        page.locator('.welcome-signup').click()
        page.wait_for_url(runtime.origin + '/')
        expect(page.locator('#kids .big')).to_have_text('$7.00')
        assert ctx.cookies() != old
        assert runtime.call('/api/state', cookie='bod-local-session=' + old_cookie)[0] == 401
        tab.wait_for_function('window.__sockets.some(s => s.readyState === 3)')
        # Independent browser first-visit trial -> same identity -> actual choice UI.
        other = browser.new_context(viewport={'width': 768, 'height': 1024})
        second = other.new_page()
        second.goto(runtime.origin)
        second.locator('.welcome-signup').click()
        second.wait_for_url('**/auth/choose-bank')
        expect(second.get_by_role('heading', name='You already have a family bank')).to_be_visible()
        assert second.evaluate('document.documentElement.scrollWidth <= innerWidth')
        second.get_by_role('button', name='Open my existing family bank').click()
        second.wait_for_url(runtime.origin + '/')
        open_parent(second)
        second.locator('.dad-account summary').click()
        second.get_by_role('button', name='Return to this device’s trial').click()
        second.wait_for_url(runtime.origin + '/')
        expect(second.locator('#kids .big')).to_have_text('$7.00')
        assert errors == []



def directory_database(runtime):
    for path in (runtime.base / 'state').rglob('*.sqlite'):
        with sqlite3.connect(path) as sql:
            if sql.execute("SELECT 1 FROM sqlite_master WHERE name='identities'").fetchone():
                return path
    raise AssertionError('Directory not persisted')


def test_state_expiry_and_expired_guest_claim_preserve_original_trial(oauth):
    runtime, provider = oauth
    provider.subject, provider.mode = 'expired-trial-parent', 'ok'
    with client(runtime) as c:
        csrf(c)
        old = c.cookies['bod-local-session']
        callback = start_login(c, provider)
        count = provider.calls.count('token')
        runtime.stop()
        with sqlite3.connect(directory_database(runtime)) as sql:
            sql.execute('UPDATE login_states SET expires=0')
        path = runtime.database('bod-local-session=' + old)
        with sqlite3.connect(path) as sql:
            sql.execute('UPDATE _bod_family SET trial_until=?', (time.time() - 60,))
            dates = sql.execute('SELECT trial_started,trial_until FROM _bod_family').fetchone()
        runtime.start()
        assert c.get(callback).status_code == 400
        assert provider.calls.count('token') == count
        assert c.get('/api/state').status_code == 402
        login(c, provider, 'expired-trial-parent')
        assert c.cookies['bod-local-session'].split('.')[0] == old.split('.')[0]
        assert c.get('/api/state').status_code == 402
        assert c.get('/billing').status_code == 200
        with sqlite3.connect(path) as sql:
            assert sql.execute('SELECT trial_started,trial_until FROM _bod_family').fetchone() == dates
            assert sql.execute('SELECT wallet_cents FROM kids WHERE id=1').fetchone()[0] == 500


def test_competing_claims_only_one_identity_can_own_original_bank(oauth):
    runtime, provider = oauth
    with client(runtime) as first, client(runtime) as second:
        csrf(first)
        old = first.cookies['bod-local-session']
        second.cookies.set('bod-local-session', old, domain='127.0.0.1', path='/')
        provider.subject, provider.mode = 'racing-parent-a', 'ok'
        a = start_login(first, provider)
        provider.subject = 'racing-parent-b'
        b = start_login(second, provider)
        with ThreadPoolExecutor(2) as pool:
            x, y = list(pool.map(lambda pair: pair[0].get(pair[1]), [(first, a), (second, b)]))
        assert sorted([x.status_code, y.status_code]) == [302, 409]
        winner = first if x.status_code == 302 else second
        assert winner.cookies['bod-local-session'].split('.')[0] == old.split('.')[0]
        assert bank(winner)['kids'][0]['wallet_cents'] == 500
        with sqlite3.connect(directory_database(runtime)) as sql:
            rows = sql.execute('SELECT subject FROM identities WHERE family=?', (old.split('.')[0],)).fetchall()
            assert len(rows) == 1 and rows[0][0] in ('racing-parent-a', 'racing-parent-b')
        assert runtime.call('/api/state', cookie='bod-local-session=' + old)[0] == 401


def test_provider_wait_does_not_hold_durable_sql_transaction(oauth):
    runtime, provider = oauth
    with client(runtime) as c, client(runtime) as other:
        token = csrf(c)
        setup_parent(c, runtime)
        provider.subject, provider.mode = 'slow-provider-parent', 'slow'
        callback = start_login(c, provider)
        provider.token_entered.clear()
        provider.token_release.clear()
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(c.get, callback)
            try:
                assert provider.token_entered.wait(10)
                provider.mode = 'ok'
                # Same directory can durably save another state while token HTTP
                # is blocked, and the real bank can still commit a gift.
                assert other.get('/auth/login').status_code == 302
                assert c.post('/api/admin/gift', json=dict(kid_id=1, amount_cents=17),
                              headers={'Origin': runtime.origin, 'X-CSRF-Token': token}).status_code == 200
            finally:
                provider.token_release.set()
            assert future.result().status_code == 302
        assert bank(c)['kids'][0]['wallet_cents'] == 517


def test_claim_recovers_after_real_directory_session_commit_failure(oauth):
    runtime, provider = oauth
    with client(runtime) as c:
        csrf(c)
        original = c.cookies['bod-local-session']
        provider.subject, provider.mode = 'recover-reserved-owner', 'ok'
        callback = start_login(c, provider)
        runtime.stop()
        directory = directory_database(runtime)
        with sqlite3.connect(directory) as sql:
            sql.execute("CREATE TRIGGER fail_session BEFORE INSERT ON sessions BEGIN SELECT RAISE(ABORT, 'injected session failure'); END")
        runtime.start()
        assert c.get(callback).status_code == 503
        assert c.cookies['bod-local-session'] == original
        assert c.get('/api/state').status_code == 401  # privilege transfer already revoked guest
        runtime.stop()
        with sqlite3.connect(directory) as sql:
            sql.execute('DROP TRIGGER fail_session')
            assert sql.execute('SELECT family FROM identities WHERE subject=?', ('recover-reserved-owner',)).fetchone()[0] == original.split('.')[0]
        runtime.start()
        login(c, provider, 'recover-reserved-owner')
        assert c.cookies['bod-local-session'].split('.')[0] == original.split('.')[0]
        assert bank(c)['kids'][0]['wallet_cents'] == 500


def test_collision_response_loss_and_restart_recover_device_bank(oauth):
    runtime, provider = oauth
    with client(runtime) as owner, client(runtime) as guest, client(runtime) as recovery:
        login(owner, provider, 'recover-switch-owner')
        token = csrf(guest)
        original = guest.cookies['bod-local-session']
        login(guest, provider, 'recover-switch-owner')
        choice = guest.cookies['bod-bank-choice']
        recovery.cookies.set('bod-local-session', original, domain='127.0.0.1', path='/')
        recovery.cookies.set('bod-bank-choice', choice, domain='127.0.0.1', path='/')
        assert guest.post('/auth/choose-bank', data={'choice': 'existing', 'csrf_token': token},
                          headers={'Origin': runtime.origin}).status_code == 302
        signed = guest.cookies['bod-local-session']
        runtime.stop()
        runtime.start()
        assert recovery.get('/auth/choose-bank').status_code == 302
        assert recovery.cookies['bod-local-session'] == signed
        return_csrf = csrf(recovery)
        assert recovery.post('/auth/device-bank', data={'csrf_token': return_csrf},
                             headers={'Origin': runtime.origin}).status_code == 302
        device = recovery.cookies['bod-local-session']
        # Reproduce a lost return response by retrying with the revoked old
        # signed cookie and the originally authorized CSRF form.
        with client(runtime) as retry:
            retry.cookies.set('bod-local-session', signed, domain='127.0.0.1', path='/')
            assert retry.get('/api/state').status_code == 401
            assert retry.post('/auth/device-bank', data={'csrf_token': return_csrf},
                              headers={'Origin': runtime.origin}).status_code == 302
            assert retry.cookies['bod-local-session'] == device
        assert recovery.cookies['bod-local-session'].split('.')[0] == original.split('.')[0]
        assert bank(recovery)['kids'][0]['wallet_cents'] == 500


def test_signed_session_idle_alarm_and_logout_are_device_scoped(oauth):
    from websockets.sync.client import connect
    from websockets.exceptions import ConnectionClosed
    runtime, provider = oauth
    with client(runtime) as first, client(runtime) as second:
        login(first, provider, 'session-expiry-owner')
        login(second, provider, 'session-expiry-owner')
        a, b = first.cookies['bod-local-session'], second.cookies['bod-local-session']
        csrf_second = csrf(second)
        runtime.stop()
        path = runtime.database('bod-local-session=' + a)
        with sqlite3.connect(path) as sql:
            sql.execute('UPDATE _bod_sessions SET expires=? WHERE hash=?',
                        (time.time() + 60, hashlib.sha256(a.split('.')[1].encode()).hexdigest()))
        runtime.start()
        url = runtime.origin.replace('http:', 'ws:') + '/api/updates'
        with connect(url, origin=runtime.origin, additional_headers={'Cookie': 'bod-local-session=' + a}) as ws1:
            assert json.loads(ws1.recv(timeout=5))['type'] == 'connected'
            with connect(url, origin=runtime.origin, additional_headers={'Cookie': 'bod-local-session=' + b}) as ws2:
                assert json.loads(ws2.recv(timeout=5))['type'] == 'connected'
                with pytest.raises(ConnectionClosed) as expired:
                    while True:
                        ws1.recv(timeout=75)
                assert expired.value.rcvd.code == 1008
                assert first.get('/api/state').status_code == 401
                assert bank(second)['kids'][0]['wallet_cents'] == 500
                assert second.post('/auth/logout', data={'csrf_token': csrf_second},
                                   headers={'Origin': runtime.origin}).status_code == 302
                with pytest.raises(ConnectionClosed) as revoked:
                    while True:
                        ws2.recv(timeout=20)
                assert revoked.value.rcvd.code == 1008
        with sqlite3.connect(directory_database(runtime)) as sql:
            assert not sql.execute('SELECT 1 FROM sessions WHERE hash=?',
                                   (hashlib.sha256(b.split('.')[1].encode()).hexdigest(),)).fetchone()
