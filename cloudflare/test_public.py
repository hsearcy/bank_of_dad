"""Public TLS boundary in real local workerd, with isolated persistence.

Wrangler's local HTTPS listener models Cloudflare TLS termination with a real
TLS connection and explicit Host. No DNS changes, deployment or real provider
accounts are used. Self-signed local certificate verification is test-only.
"""
import re
import sqlite3
import ssl
from types import SimpleNamespace

import httpx
import pytest

from test_runtime import Runtime
from cloudflare_security import cookie_header, ingress_status, runtime_mode

PUBLIC = 'https://thebankofdad.app'
VARS = {'BOD_PUBLIC_ORIGIN': PUBLIC, 'BOD_LOCAL_ONLY': 'false',
        'BOD_PUBLIC_PILOT': 'true', 'BOD_BILLING_MODE': 'unavailable'}


class PublicRuntime(Runtime):
    def __init__(self, base, variables=None, **kwargs):
        super().__init__(base, {**VARS, **(variables or {})},
                         options=['--local-protocol', 'https', '--infer-origin-from-routes', 'false'], **kwargs)
        self.origin = self.origin.replace('http:', 'https:')
        self.ssl_context = ssl._create_unverified_context()

    def call(self, path, *, origin=True, headers=None, **kwargs):
        return super().call(path, origin=PUBLIC if origin is True else origin,
                            headers={'Host': 'thebankofdad.app', 'CF-Connecting-IP': '192.0.2.42',
                                     **(headers or {})}, **kwargs)


def env(**values):
    return SimpleNamespace(**{**VARS, **values})


def request(url=PUBLIC + '/', method='GET', headers=None):
    return SimpleNamespace(url=url, method=method, headers={'Host': 'thebankofdad.app', **(headers or {})})


@pytest.mark.parametrize('bad', [PUBLIC + '/', PUBLIC + '/path', PUBLIC + '?x=1',
    'http://thebankofdad.app', 'https://user@thebankofdad.app', 'https://localhost',
    'https://127.0.0.1:8787', 'https://thebankofdad.app:443'])
def test_exact_public_origin_required(bad):
    with pytest.raises(ValueError):
        runtime_mode(env(BOD_PUBLIC_ORIGIN=bad))


@pytest.mark.parametrize('values', [
    {'BOD_PUBLIC_PILOT': 'false'}, {'BOD_BILLING_MODE': 'live'},
    {'BOD_LOCAL_ONLY': 'true'}, {'BOD_TEST_OIDC_ORIGIN': 'http://127.0.0.1:9999'},
    {'BOD_TEST_STRIPE_ORIGIN': 'http://127.0.0.1:9999'},
    {'BOD_STRIPE_SECRET_KEY': 'sk_test_not-a-key'},
    {'BOD_STRIPE_SECRET_KEY': 'sk_live_not-a-key'}, {'BOD_STRIPE_LIVEMODE': 'false'}])
def test_public_forbids_overrides_and_sandbox(values):
    with pytest.raises(ValueError):
        runtime_mode(env(**values))


def test_authoritative_ingress_and_all_cookie_lifecycles():
    assert ingress_status(env(), request()) == 0
    assert ingress_status(env(), request(headers={'Host': 'evil.invalid'})) == 503
    assert ingress_status(env(), request(url='http://thebankofdad.app/', headers={
        'X-Forwarded-Proto': 'https', 'Forwarded': 'proto=https;host=thebankofdad.app'})) == 503
    assert ingress_status(env(), request(headers={'Origin': 'null'})) == 403
    assert ingress_status(env(), request(headers={'Origin': 'https://evil.invalid'})) == 403
    assert ingress_status(env(), request(method='POST')) == 403
    assert ingress_status(env(), request(method='POST', headers={'Origin': PUBLIC})) == 0
    assert ingress_status(env(), request(url=PUBLIC + '/billing/webhook', method='POST')) == 0
    for name in ('bod-local-session', 'bod-oauth-browser', 'bod-bank-choice'):
        for value, age in (('new-secret', 600), ('rotated-secret', 2592000), ('', 0)):
            line = cookie_header(env(), name, value, age)
            assert all(x in line for x in ('; Secure', '; HttpOnly', '; SameSite=Lax', '; Path=/', f'Max-Age={age}'))
            assert 'Domain=' not in line
            local = cookie_header(env(BOD_LOCAL_ONLY='true', BOD_PUBLIC_ORIGIN='http://127.0.0.1:8787'), name, value, age)
            assert '; Secure' not in local


@pytest.fixture
def public(tmp_path):
    server = PublicRuntime(tmp_path)
    server.start()
    try:
        yield server
    finally:
        server.stop()


def family_databases(server):
    result = set()
    for path in (server.base / 'state').rglob('*.sqlite'):
        with sqlite3.connect(path) as c:
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='_bod_family'").fetchone():
                result.add(path)
    return result


def assert_secure(header, *, deleted=False):
    assert all(x in header for x in ('; Secure', '; HttpOnly', '; SameSite=Lax', '; Path=/'))
    assert 'Domain=' not in header
    if deleted:
        assert 'Max-Age=0' in header


def test_workerd_public_bank_cookies_host_origin_and_trial(public):
    code, html, headers = public.call('/')
    assert code == 200, html
    assert_secure(headers['Set-Cookie'])
    assert 'Private pilot: payments are unavailable.' in html
    assert 'you can subscribe for $9.99/year' not in html
    assert 'mailto:support@thebankofdad.app' in html
    cookie = headers['Set-Cookie'].split(';')[0]
    csrf = re.search(r'name="csrf-token" content="([0-9a-f]+)"', html)[1]
    assert public.call('/api/state', cookie=cookie)[0] == 200
    for path in ('/', '/static/style.css', '/auth/login', '/auth/callback', '/owner', '/billing/webhook'):
        assert public.call(path, cookie=cookie, headers={'Host': 'foreign.invalid'})[0] == 503
        assert public.call(path, cookie=cookie, origin='https://foreign.invalid')[0] == 403
    for origin in (False, 'null', 'https://foreign.invalid'):
        assert public.call('/api/admin/gift', cookie=cookie, csrf=csrf, origin=origin,
                           body={'kid_id': 1, 'amount_cents': 99})[0] == 403
    assert public.call('/api/admin/gift', cookie=cookie, csrf=csrf,
                       body={'kid_id': 1, 'amount_cents': 99})[0] == 200
    billing = public.call('/billing', cookie=cookie)[1]
    assert 'payments unavailable' in billing
    assert 'action="/billing/checkout"' not in billing
    code, unavailable, _ = public.call('/billing/checkout', cookie=cookie, csrf=csrf, body={})
    assert code == 503
    assert 'original 31-day trial' in unavailable
    assert 'seven-day' not in unavailable and '7-day' not in unavailable
    assert public.call('/billing/webhook', origin=False, body={})[0] == 503
    path = public.database(cookie)
    with sqlite3.connect(path) as c:
        started, until, free = c.execute('SELECT trial_started,trial_until,free_access FROM _bod_family').fetchone()
        assert until - started == 31 * 86400 and free == 0
    with httpx.Client(base_url=public.origin, verify=False) as c:
        result = c.post('/auth/logout', headers={'Host': 'thebankofdad.app', 'Origin': PUBLIC,
            'CF-Connecting-IP': '192.0.2.42', 'Cookie': cookie, 'X-CSRF-Token': csrf})
    assert result.status_code == 302
    assert_secure(result.headers['set-cookie'], deleted=True)
    assert public.call('/api/state', cookie=cookie)[0] == 401


def test_workerd_abuse_budget_rejects_before_new_do_initialization(public):
    cookies = []
    for _ in range(3):
        code, _, headers = public.call('/')
        assert code == 200
        cookies.append(headers['Set-Cookie'].split(';')[0])
    before = family_databases(public)
    code, _, headers = public.call('/')
    assert code == 429
    assert 'Set-Cookie' not in headers
    assert family_databases(public) == before
    assert public.call('/api/state', cookie=cookies[0])[0] == 200


@pytest.mark.parametrize('override', [
    {'BOD_TEST_OIDC_ORIGIN': 'http://127.0.0.1:9999'},
    {'BOD_STRIPE_SECRET_KEY': 'sk_test_not-a-key'}, {'BOD_PUBLIC_PILOT': 'false'}])
def test_workerd_bad_public_config_fails_before_assets_or_storage(tmp_path, override):
    server = PublicRuntime(tmp_path, override, readiness_status=503)
    server.start()
    try:
        assert server.call('/')[0] == 503
        assert family_databases(server) == set()
    finally:
        server.stop()


def test_workerd_secure_choice_session_rotation_and_device_return(tmp_path):
    # Establish synthetic identities in local fixture mode, then REMOVE provider
    # overrides and restart the exact same disposable SQLite state in public mode.
    # No production test hook or weakened provider verifier is introduced.
    from test_oauth import Provider, client, login, csrf
    provider = Provider()
    local = Runtime(tmp_path, {'BOD_OIDC_CLIENT_ID': 'fixture-client',
        'BOD_OIDC_CLIENT_SECRET': 'fixture-secret', 'BOD_TEST_OIDC_ORIGIN': provider.origin})
    local.start()
    try:
        with client(local) as owner, client(local) as guest:
            owner.get('/')
            login(owner, provider, 'public-cookie-parent')
            token = csrf(guest)
            original = guest.cookies['bod-local-session']
            login(guest, provider, 'public-cookie-parent')
            choice = guest.cookies['bod-bank-choice']
    finally:
        local.stop()
        provider.stop()
    path = local.database('bod-local-session=' + original)
    with sqlite3.connect(path) as c:
        # Expired guest trial must remain expired through session rotation.
        c.execute('UPDATE _bod_family SET trial_until=trial_started-1')
        dates = c.execute('SELECT trial_started,trial_until FROM _bod_family').fetchone()
    server = PublicRuntime(tmp_path)
    server.start()
    try:
        with httpx.Client(base_url=server.origin, verify=False, headers={
                'Host': 'thebankofdad.app', 'Origin': PUBLIC, 'CF-Connecting-IP': '192.0.2.42'}) as c:
            result = c.post('/auth/choose-bank', headers={'Cookie':
                f'bod-local-session={original}; bod-bank-choice={choice}'},
                data={'choice': 'existing', 'csrf_token': token})
            assert result.status_code == 302, result.text
            for header in result.headers.get_list('set-cookie'):
                assert_secure(header, deleted=header.startswith('bod-bank-choice='))
            signed = result.cookies['bod-local-session']
            html = server.call('/', cookie='bod-local-session=' + signed)[1]
            signed_csrf = re.search(r'name="csrf-token" content="([0-9a-f]+)"', html)[1]
            result = c.post('/auth/device-bank', headers={'Cookie': 'bod-local-session=' + signed},
                            data={'csrf_token': signed_csrf})
            assert result.status_code == 302, result.text
            assert_secure(result.headers['set-cookie'])
            returned = result.cookies['bod-local-session']
            assert returned != original and returned.split('.')[0] == original.split('.')[0]
            assert server.call('/api/state', cookie='bod-local-session=' + signed)[0] == 401
            assert server.call('/api/state', cookie='bod-local-session=' + original)[0] == 401
            assert server.call('/api/state', cookie='bod-local-session=' + returned)[0] == 402
        with sqlite3.connect(path) as c:
            assert c.execute('SELECT trial_started,trial_until FROM _bod_family').fetchone() == dates
    finally:
        server.stop()
