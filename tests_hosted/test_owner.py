"""Private owner UI against disposable state and signed synthetic Google login."""
import json
import sqlite3
import time
from pathlib import Path

import pytest

from test_hosted import hosted_app, call, login, csrf, post, signed_event, subscription
from owner_panel import owner_allowlist


@pytest.fixture
def owner_app(monkeypatch, request):
    monkeypatch.setenv('BOD_OWNER_IDENTITIES', json.dumps([
        {'issuer': 'https://accounts.google.com', 'subject': 'owner-synthetic'}]))
    return request.getfixturevalue('hosted_app')


def snapshot(host):
    with host.connect() as conn:
        return '\n'.join(conn.iterdump())


def family(host, subject):
    with host.connect() as conn:
        return dict(conn.execute('SELECT * FROM families WHERE subject=?', (subject,)).fetchone())


def owner_post(client, origin, fid, action='grant', reason='Synthetic support request', **extra):
    return call(client, '/owner/access', origin, method='POST',
                headers={'Origin': origin}, data={'family': fid, 'action': action,
                'reason': reason, 'csrf_token': csrf(client), **extra})


@pytest.mark.parametrize('value', ['', '[]'])
def test_owner_disabled_by_default(hosted_app, value):
    app, host, state, origin = hosted_app
    host.owner_panel.owners = owner_allowlist(value)
    client = app.test_client()
    login(client, state, origin)
    before = snapshot(host)
    for path, method in [('/owner', 'GET'), ('/owner/access', 'POST'), ('/api/owner/families', 'GET')]:
        assert call(client, path, origin, method=method).status_code == 404
    assert snapshot(host) == before


@pytest.mark.parametrize('value', ['{}', 'null', '[null]', '[{"issuer":"accounts.google.com","subject":"x"}]',
    '[{"issuer":"https://evil.example","subject":"x"}]', '[{"issuer":"https://accounts.google.com","subject":""}]',
    '[{"issuer":"https://accounts.google.com","subject":"x","email":"owner@example.test"}]'])
def test_owner_config_fail_closed(value):
    with pytest.raises(ValueError, match='BOD_OWNER_IDENTITIES'):
        owner_allowlist(value)


def test_only_verified_allowlisted_identity_can_read_or_write(owner_app):
    app, host, state, origin = owner_app
    owner, parent, guest, anonymous = [app.test_client() for _ in range(4)]
    assert login(owner, state, origin, 'owner-synthetic').status_code == 302
    assert login(parent, state, origin, 'parent-a').status_code == 302
    call(guest, '/', origin)
    fid = family(host, 'parent-a')['id']
    before = snapshot(host)
    assert call(anonymous, '/owner', origin).status_code == 401
    for client in (parent, guest, anonymous):
        assert call(client, '/owner', origin).status_code in (401, 403)
        assert call(client, '/api/owner/families', origin).status_code == 403
        assert call(client, '/owner/access', origin, method='POST', headers={'Origin': origin},
                    data={'family': fid, 'action': 'grant', 'reason': 'forged',
                          'role': 'owner', 'email': 'owner@example.test'}).status_code == 403
    # Even a legitimate CSRF token and a fake client/session role never confer owner authority.
    with parent.session_transaction(base_url=origin) as current:
        current['role'] = 'owner'
    assert owner_post(parent, origin, fid).status_code == 403
    assert snapshot(host) == before
    response = call(owner, '/owner', origin)
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    assert fid.encode() in response.data
    for private in ('parent-a', 'owner-synthetic', 'guest_hash', 'checkout_url', 'csrf_token='):
        assert private.encode() not in response.data
    # Owner is an allowlisted verified issuer+subject, not merely a subject match.
    host.owner_panel.owners = frozenset({('https://wrong.example', 'owner-synthetic')})
    assert call(owner, '/owner', origin).status_code == 403
    assert snapshot(host) == before


def test_owner_actions_csrf_origin_validation_and_rollback(owner_app):
    app, host, state, origin = owner_app
    client = app.test_client()
    login(client, state, origin, 'owner-synthetic')
    fid = family(host, 'owner-synthetic')['id']
    token = csrf(client)
    before = snapshot(host)
    for headers, data in [
        ({'Origin': origin}, {'csrf_token': ''}),
        ({'Origin': origin}, {'csrf_token': 'wrong'}),
        ({'Origin': origin}, {'csrf_token': '非ascii'}),
        ({'Origin': 'https://evil.example'}, {'csrf_token': token}),
        ({'Origin': 'null'}, {'csrf_token': token}),
        ({}, {'csrf_token': token}),
    ]:
        assert call(client, '/owner/access', origin, method='POST', headers=headers,
                    data={'family': fid, 'action': 'grant', 'reason': 'test', **data}).status_code == 403
        assert snapshot(host) == before
    for bad in ({'reason': ' '}, {'reason': 'x' * 301}, {'family': '../invalid'}, {'action': 'cancel'}):
        values = dict(fid=fid, action='grant', reason='test')
        if 'family' in bad:
            bad = {'fid': bad['family']}
        values.update(bad)
        assert owner_post(client, origin, **values).status_code == 400
        assert snapshot(host) == before
    assert owner_post(client, origin, 'f' * 32).status_code == 404
    assert owner_post(client, origin, fid, 'revoke').status_code == 400
    assert snapshot(host) == before
    with host.connect() as conn:
        conn.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON access_audit BEGIN SELECT RAISE(ABORT, 'synthetic audit failure'); END")
    before = snapshot(host)
    assert owner_post(client, origin, fid).status_code == 503
    assert snapshot(host) == before
    assert family(host, 'owner-synthetic')['free_access'] == 0


def test_grant_revoke_readback_paid_preservation_and_no_provider_calls(owner_app):
    app, host, state, origin = owner_app
    owner, target = app.test_client(), app.test_client()
    login(owner, state, origin, 'owner-synthetic')
    login(target, state, origin, 'parent-a')
    fid = family(host, 'parent-a')['id']
    with host.connect() as conn:
        conn.execute('UPDATE families SET trial_until=0 WHERE id=?', (fid,))
    bank_file = Path(host.family_path(fid))
    bank_before = bank_file.read_bytes()
    original = family(host, 'parent-a')
    assert call(target, '/api/state', origin).status_code == 402
    response = owner_post(owner, origin, fid)
    assert response.status_code == 303
    page = call(owner, response.location, origin)
    assert page.status_code == 200 and b'Free access' in page.data
    assert call(target, '/api/state', origin).status_code == 200
    assert family(host, 'parent-a')['free_access'] == 1
    assert owner_post(owner, origin, fid, 'revoke', confirm_revoke='yes').status_code == 303
    assert call(target, '/api/state', origin).status_code == 402
    assert family(host, 'parent-a')['free_access'] == 0
    assert bank_file.read_bytes() == bank_before
    assert family(host, 'parent-a') == original
    assert state['subscription_calls'] == 0 and not state['customers']
    with host.connect() as conn:
        audit = [dict(r) for r in conn.execute('SELECT * FROM access_audit ORDER BY id')]
    assert [a['action'] for a in audit] == ['grant', 'revoke']
    assert all(a['family'] == fid and a['reason'] == 'Synthetic support request'
               and a['operator'] == 'oidc:https://accounts.google.com:owner-synthetic' for a in audit)
    # A real SDK reconciliation against the synthetic provider records its timestamp.
    with host.connect() as conn:
        conn.execute('UPDATE families SET customer=? WHERE id=?', ('cus_paid', fid))
    state['subscriptions']['cus_paid'] = [subscription()]
    assert signed_event(owner, origin, customer='cus_paid').status_code == 200
    paid = family(host, 'parent-a')
    calls = state['subscription_calls']
    assert paid['billing_reconciled_at'] > 0
    assert owner_post(owner, origin, fid).status_code == 303
    page = call(owner, '/owner?family=' + fid, origin)
    assert b'Paid access' in page.data and b'free grant will still apply' in page.data
    assert owner_post(owner, origin, fid, 'revoke', confirm_revoke='yes').status_code == 303
    assert family(host, 'parent-a') == paid
    assert call(target, '/api/state', origin).status_code == 200
    assert state['subscription_calls'] == calls
    # Reconciliation must never erase a separate free grant.
    assert owner_post(owner, origin, fid).status_code == 303
    state['subscriptions']['cus_paid'] = []
    assert signed_event(owner, origin, event_id='evt_ended', customer='cus_paid').status_code == 200
    assert family(host, 'parent-a')['free_access'] == 1
    assert call(target, '/api/state', origin).status_code == 200


def test_list_search_pagination_and_clock_derived_status(owner_app, monkeypatch):
    app, host, state, origin = owner_app
    client = app.test_client()
    login(client, state, origin, 'owner-synthetic')
    now = time.time()
    with host.connect() as conn:
        for i in range(51):
            conn.execute('''INSERT INTO families(id, issuer, subject, trial_started, trial_until, paid_until)
                         VALUES (?, ?, ?, ?, ?, ?)''',
                         (f'{i:032x}', 'https://accounts.google.com', f'synthetic-{i}', now - 100, now + 100, now + 50 if i == 0 else 0))
    before = snapshot(host)
    page = call(client, '/owner', origin)
    assert page.data.count(b'<article ') == 50 and b'>Next</a>' in page.data
    second = call(client, '/owner?page=2', origin)
    assert second.data.count(b'<article ') == 2
    exact = '/owner?family=' + '0' * 32
    assert call(client, exact, origin).data.count(b'<article ') == 1
    assert b'Paid access</span>' in call(client, exact, origin).data
    monkeypatch.setattr(time, 'time', lambda: now + 50)
    assert b'Trial</span>' in call(client, exact, origin).data
    monkeypatch.setattr(time, 'time', lambda: now + 100)
    assert b'Expired</span>' in call(client, exact, origin).data
    assert snapshot(host) == before
    assert call(client, '/owner?family=%25', origin).status_code == 400
    assert call(client, '/owner?family=NOT-A-FAMILY', origin).status_code == 400
    assert call(client, '/owner?page=invalid', origin).status_code == 400
    assert call(client, '/owner?family=' + 'f' * 32, origin).data.count(b'<article ') == 0
    host.owner_panel.owners = frozenset()
    assert call(client, '/owner', origin).status_code == 404


def test_owner_cannot_be_impersonated_by_email_claim(owner_app):
    app, host, state, origin = owner_app
    client = app.test_client()
    state['claims'] = {'email': 'owner-synthetic', 'role': 'owner'}
    login(client, state, origin, 'not-the-owner')
    assert call(client, '/owner', origin).status_code == 403
    # Even allowlisted sub with an invalid signed issuer fails at callback.
    state['claims'] = {'iss': 'https://attacker.example'}
    assert login(client, state, origin, 'owner-synthetic').status_code == 400
    assert call(client, '/owner', origin).status_code == 401


def test_free_mode_does_not_register_owner_routes(tmp_path):
    import os
    import subprocess
    import sys
    env = {**os.environ, 'BANK_OF_DAD_MODE': 'self-hosted',
           'BANK_OF_DAD_DB': str(tmp_path / 'free.sqlite'), 'BOD_OWNER_IDENTITIES': 'malformed'}
    result = subprocess.run([sys.executable, '-c', "import app; c=app.app.test_client(); assert c.get('/owner').status_code == 404; assert c.post('/owner/access').status_code == 404; assert c.get('/api/state').status_code == 200; print('free mode unaffected')"],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_expired_owner_family_still_admin_and_logout_revokes(owner_app):
    app, host, state, origin = owner_app
    client = app.test_client()
    login(client, state, origin, 'owner-synthetic')
    with host.connect() as conn:
        conn.execute('UPDATE families SET trial_until=0, paid_until=0, free_access=0')
    assert call(client, '/api/state', origin).status_code == 402
    assert call(client, '/owner', origin).status_code == 200
    cookie = client.get_cookie('__Host-bod-session', domain='bank.example.test')
    assert post(client, '/auth/logout', origin).status_code == 302
    replay = app.test_client()
    replay.set_cookie('__Host-bod-session', cookie.value, domain='bank.example.test')
    assert call(replay, '/owner', origin).status_code == 401
    before = snapshot(host)
    assert call(replay, '/owner/access', origin, method='POST').status_code == 403
    assert snapshot(host) == before


def test_reconciliation_date_migration_is_additive_atomic_and_idempotent(tmp_path):
    from hosted_access import migrate
    class Interrupted(sqlite3.Connection):
        fail = False
        def execute(self, sql, *args):
            if self.fail and sql.startswith('CREATE TABLE IF NOT EXISTS access_audit'):
                raise sqlite3.OperationalError('Synthetic failure after reconciliation column')
            return super().execute(sql, *args)
    conn = sqlite3.connect(tmp_path / 'synthetic-legacy.sqlite', factory=Interrupted)
    try:
        with conn:
            conn.execute('''CREATE TABLE families (id TEXT PRIMARY KEY, paid_until REAL,
                            trial_started REAL, trial_until REAL, free_access INTEGER)''')
            conn.execute('INSERT INTO families VALUES (?, ?, ?, ?, ?)', ('a' * 32, 12345, 100, 200, 1))
        before = '\n'.join(conn.iterdump())
        conn.fail = True
        with pytest.raises(sqlite3.OperationalError):
            with conn:
                migrate(conn, now=300)
        assert '\n'.join(conn.iterdump()) == before
        conn.fail = False
        with conn:
            migrate(conn, now=400)
        assert conn.execute('SELECT * FROM families').fetchone() == ('a' * 32, 12345, 100, 200, 1, None)
        after = '\n'.join(conn.iterdump())
        with conn:
            migrate(conn, now=500)
        assert '\n'.join(conn.iterdump()) == after
    finally:
        conn.close()
