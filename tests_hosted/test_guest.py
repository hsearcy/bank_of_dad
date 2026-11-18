"""Anonymous onboarding uses real family storage and the real OIDC callback."""
import sqlite3
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import threading
from urllib.parse import urlsplit

import requests
import pytest

from test_hosted import hosted_app, call, post, login


def current(client, origin):
    with client.session_transaction(base_url=origin) as session:
        return dict(session)


def snapshot(host, family):
    with sqlite3.connect(host.family_path(family)) as conn:
        return {table: conn.execute('SELECT * FROM ' + table).fetchall()
                for table in ('kids', 'bonds', 'transactions', 'history', 'settings', 'sqlite_sequence')}


@pytest.mark.parametrize('hosted_app', ['https://accounts.google.com', 'https://identity.example.test'], indirect=True)
def test_guest_real_seed_mutations_reload_isolation_and_claim(hosted_app):
    app, host, state, origin = hosted_app
    a, b = app.test_client(), app.test_client()
    assert call(a, '/', origin).status_code == 200
    guest = current(a, origin)
    kid = call(a, '/api/state', origin).json['kids'][0]
    assert (kid['wallet_cents'], kid['savings_cents']) == (500, 100)
    assert post(a, '/api/spend', origin, json={'kid_id': kid['id'], 'amount_cents': 100, 'reason': 'Test snack'}).json['ok']
    assert post(a, '/api/admin/kid', origin, json={'name': 'Another kid'}).json['ok']
    assert post(a, '/api/admin/rates', origin, json={'savings_weekly_pct': 3}).json['ok']
    assert call(a, '/', origin).status_code == 200
    assert call(a, '/api/state', origin).json['kids'][0]['wallet_cents'] == 400
    assert call(b, '/', origin).status_code == 200
    assert current(b, origin)['family'] != guest['family']
    assert call(b, '/api/state?family=' + guest['family'], origin).json['kids'][0]['wallet_cents'] == 500
    before = snapshot(host, guest['family'])
    with host.connect() as conn:
        trial = conn.execute('SELECT trial_until FROM families WHERE id=?', (guest['family'],)).fetchone()[0]
    cookie = a.get_cookie('__Host-bod-session', domain='bank.example.test')
    assert login(a, state, origin).status_code == 302
    assert current(a, origin)['family'] == guest['family']
    assert 'guest' not in current(a, origin)
    assert snapshot(host, guest['family']) == before
    with host.connect() as conn:
        row = conn.execute('SELECT * FROM families WHERE id=?', (guest['family'],)).fetchone()
        assert row['guest_hash'] is None and row['trial_until'] == trial
    replay = app.test_client()
    replay.set_cookie('__Host-bod-session', cookie.value, domain='bank.example.test')
    assert call(replay, '/api/state', origin).status_code == 401
    assert state['customers'] == state['checkouts'] == []
    assert not Path(__import__('db').DB_PATH).exists()


def test_guest_expiry_keeps_claim_contact_but_not_mutations_or_anonymous_billing(hosted_app):
    app, host, state, origin = hosted_app
    a = app.test_client()
    call(a, '/', origin)
    family = current(a, origin)['family']
    with host.connect() as conn:
        conn.execute('UPDATE families SET trial_until=0 WHERE id=?', (family,))
    assert call(a, '/', origin).location == '/billing'
    assert call(a, '/api/state', origin).status_code == 402
    assert post(a, '/api/admin/kid', origin, json={'name': 'No'}).status_code == 402
    page = call(a, '/billing', origin)
    assert b'Save this bank' in page.data and b'/auth/login' in page.data
    assert post(a, '/billing/checkout', origin).location == '/auth/login'
    assert not state['customers']
    assert login(a, state, origin).status_code == 302
    assert current(a, origin)['family'] == family
    assert call(a, '/api/state', origin).status_code == 402


def test_existing_family_collision_preserves_both_and_requires_choice(hosted_app):
    app, host, state, origin = hosted_app
    owner = app.test_client()
    login(owner, state, origin)
    post(owner, '/api/admin/kid', origin, json={'name': 'Original family'})
    existing = current(owner, origin)['family']
    a = app.test_client()
    call(a, '/', origin)
    guest = current(a, origin)['family']
    before_guest, before_existing = snapshot(host, guest), snapshot(host, existing)
    assert login(a, state, origin).location == '/auth/choose-bank'
    assert current(a, origin)['family'] == guest
    assert b'Nothing has been merged' in call(a, '/auth/choose-bank', origin).data
    assert call(a, '/auth/choose-bank', origin, method='POST', data={'choice': 'existing'}).status_code == 403
    assert post(a, '/auth/choose-bank', origin, data={'choice': 'existing'}).location == '/'
    assert current(a, origin)['family'] == existing
    assert snapshot(host, guest) == before_guest and snapshot(host, existing) == before_existing
    assert post(a, '/auth/device-bank', origin).location == '/'
    assert current(a, origin)['family'] == guest
    assert login(a, state, origin).location == '/auth/choose-bank'
    assert post(a, '/auth/choose-bank', origin, data={'choice': 'guest'}).location == '/'
    assert current(a, origin)['family'] == guest
    assert snapshot(host, guest) == before_guest


def test_guest_capability_csrf_expiration_and_welcome(hosted_app):
    app, host, state, origin = hosted_app
    a = app.test_client()
    first = call(a, '/', origin)
    assert b'id="guestWelcome"' in first.data
    assert call(a, '/auth/welcome', origin, method='POST').status_code == 403
    assert post(a, '/auth/welcome', origin).status_code == 204
    assert b'id="guestWelcome"' not in call(a, '/', origin).data
    assert call(a, '/api/spend', origin, method='POST', json={'kid_id': 1, 'amount_cents': 100}).status_code == 403
    session = current(a, origin)
    attacker = app.test_client()
    with attacker.session_transaction(base_url=origin) as forged:
        forged['family'] = session['family']
        forged['guest'] = 'wrong-capability'
        forged['guest_deadline'] = session['guest_deadline']
    assert call(attacker, '/api/state', origin).status_code == 401
    with host.connect() as conn:
        conn.execute('UPDATE families SET guest_expires=0 WHERE id=?', (session['family'],))
    assert call(a, '/api/state', origin).status_code == 401
    assert login(a, state, origin).status_code == 302
    assert current(a, origin)['family'] != session['family']


def authorize(client, state, origin, subject):
    state['subject'] = subject
    start = call(client, '/auth/login', origin)
    auth = requests.get(start.location, allow_redirects=False, timeout=3)
    callback = urlsplit(auth.headers['Location'])
    return callback.path + '?' + callback.query


def test_guest_failed_or_stolen_callback_preserves_progress_and_retry(hosted_app):
    app, host, state, origin = hosted_app
    a, thief = app.test_client(), app.test_client()
    call(a, '/', origin)
    family = current(a, origin)['family']
    before = snapshot(host, family)
    callback = authorize(a, state, origin, 'parent-a')
    assert call(thief, callback, origin).status_code == 400
    assert state['token_calls'] == 0
    state['claims'] = {'nonce': 'invalid'}
    assert call(a, callback, origin).status_code == 400
    assert call(a, '/api/state', origin).status_code == 200
    assert snapshot(host, family) == before
    state['claims'] = {}
    assert login(a, state, origin).status_code == 302
    assert current(a, origin)['family'] == family
    assert call(a, callback, origin).status_code == 400


def test_guest_one_time_claim_races_distinct_verified_principals(hosted_app):
    app, host, state, origin = hosted_app
    a, b = app.test_client(), app.test_client()
    call(a, '/', origin)
    device = current(a, origin)
    # Model two legitimate browser-bound sessions possessing this device's
    # capability. Do not rely on identity uniqueness to enforce one-time claim.
    with b.session_transaction(base_url=origin) as copied:
        copied.update(device)
    first = authorize(a, state, origin, 'parent-a')
    second = authorize(b, state, origin, 'parent-b')
    before = snapshot(host, device['family'])
    barrier = threading.Barrier(2)
    def finish(client, callback):
        barrier.wait(timeout=5)
        return call(client, callback, origin).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        left, right = pool.submit(finish, a, first), pool.submit(finish, b, second)
        assert sorted([left.result(), right.result()]) == [302, 409]
    with host.connect() as conn:
        rows = conn.execute('SELECT * FROM families').fetchall()
        assert len(rows) == 1 and rows[0]['guest_hash'] is None
        assert rows[0]['subject'] in ('parent-a', 'parent-b')
    assert snapshot(host, device['family']) == before


def test_device_cookie_survives_trial_and_logout_revokes_capability(hosted_app):
    app, host, state, origin = hosted_app
    a = app.test_client()
    call(a, '/', origin)
    cookie = a.get_cookie('__Host-bod-session', domain='bank.example.test')
    assert cookie.secure and cookie.http_only and cookie.same_site == 'Lax'
    with host.connect() as conn:
        row = conn.execute('SELECT * FROM families').fetchone()
        assert abs(cookie.expires.timestamp() - row['guest_expires']) < 2
        assert row['guest_expires'] - row['trial_until'] > 20 * 86400
    assert post(a, '/auth/logout', origin).status_code == 302
    replay = app.test_client()
    replay.set_cookie('__Host-bod-session', cookie.value, domain='bank.example.test')
    assert call(replay, '/api/state', origin).status_code == 401


def test_upgrade_from_pre_guest_schema_preserves_paid_family_and_bank(hosted_app):
    from flask import Flask
    from hosted import Hosted
    app, host, state, origin = hosted_app
    owner = app.test_client()
    login(owner, state, origin)
    post(owner, '/api/admin/kid', origin, json={'name': 'Existing paid family'})
    family = current(owner, origin)['family']
    with host.connect() as conn:
        conn.execute('UPDATE families SET paid_until=9999999999, free_access=1 WHERE id=?', (family,))
        for column in ('guest_hash', 'guest_expires', 'welcome_dismissed'):
            conn.execute('ALTER TABLE families DROP COLUMN ' + column)
        for column in ('guest_family', 'guest_hash'):
            conn.execute('ALTER TABLE login_states DROP COLUMN ' + column)
        before = dict(conn.execute('SELECT * FROM families WHERE id=?', (family,)).fetchone())
    bank_before = Path(host.family_path(family)).read_bytes()
    for index in range(2):
        restarted = Hosted(Flask('guest-upgrade-' + str(index)))
        with restarted.connect() as conn:
            after = dict(conn.execute('SELECT * FROM families WHERE id=?', (family,)).fetchone())
        assert {key: after[key] for key in before} == before
        assert after['guest_hash'] is None and after['guest_expires'] is None
        assert after['welcome_dismissed'] == 0
        assert Path(host.family_path(family)).read_bytes() == bank_before


@pytest.mark.parametrize('revoke', ['claim', 'logout', 'capability_expiry'])
def test_guest_idle_socket_revocation(hosted_app, revoke):
    from test_hosted_realtime import network, cookie, connect, invalidation
    from websocket import WebSocketBadStatusException, WebSocketConnectionClosedException
    app, host, state, origin = hosted_app
    a = app.test_client()
    call(a, '/', origin)
    family = current(a, origin)['family']
    with network(app) as base:
        saved_cookie = cookie(a)
        ws = connect(base, origin, saved_cookie)
        try:
            invalidation(ws)
            if revoke == 'claim':
                assert login(a, state, origin).status_code == 302
            elif revoke == 'logout':
                assert post(a, '/auth/logout', origin).status_code == 302
            else:
                with host.connect() as conn:
                    conn.execute('UPDATE families SET guest_expires=0 WHERE id=?', (family,))
            ws.settimeout(3)
            try:
                assert ws.recv() == ''
            except WebSocketConnectionClosedException:
                pass
            with pytest.raises(WebSocketBadStatusException) as error:
                connect(base, origin, saved_cookie)
            assert error.value.status_code == 401
        finally:
            ws.close()

