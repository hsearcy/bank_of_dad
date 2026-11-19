"""No-card trials and local-operator access, on disposable synthetic families only."""
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_hosted import hosted_app, call, login, post, signed_event, subscription


def family(host, subject='parent-a'):
    with host.connect() as conn:
        return dict(conn.execute('SELECT * FROM families WHERE subject=?', (subject,)).fetchone())


def expire(host, subject='parent-a'):
    with host.connect() as conn:
        conn.execute('UPDATE families SET trial_until=? WHERE subject=?', (time.time() - 1, subject))


def operator(host, *args, success=True):
    result = subprocess.run([sys.executable, 'hosted_access.py', '--data', str(host.root), *args],
                            capture_output=True, text=True, timeout=10)
    assert (result.returncode == 0) == success, result.stdout + result.stderr
    return result


def test_signup_trial_stays_fixed_and_cannot_be_asserted_by_client(hosted_app):
    app, host, state, origin = hosted_app
    a = app.test_client()
    before = time.time()
    assert login(a, state, origin).status_code == 302
    row = family(host)
    assert before <= row['trial_started'] <= time.time()
    assert row['trial_until'] - row['trial_started'] == 31 * 24 * 60 * 60
    assert not state['customers'] and not state['checkouts']
    assert call(a, '/', origin).status_code == 200
    assert call(a, '/api/state', origin).status_code == 200
    assert post(a, '/api/admin/kid', origin, json={'name': 'Trial child'}).json['ok']
    assert login(a, state, origin).status_code == 302
    assert family(host)['trial_until'] == row['trial_until']
    expire(host)
    bank_path = Path(host.family_path(row['id']))
    snapshot = bank_path.read_bytes()
    assert call(a, '/', origin).location == '/billing'
    assert call(a, '/api/state?free_access=1&trial_until=9999999999', origin).status_code == 402
    assert post(a, '/api/admin/gift', origin, json={
        'kid_id': 1, 'amount_cents': 100, 'free_access': True}).status_code == 402
    assert call(a, '/billing', origin).status_code == 200
    assert post(a, '/billing/refresh', origin).status_code == 302
    assert login(a, state, origin).status_code == 302
    assert call(a, '/api/state', origin).status_code == 402
    assert bank_path.read_bytes() == snapshot
    assert post(a, '/auth/logout', origin).status_code == 302


def test_exact_expiry_boundary_and_paid_precedence(hosted_app, monkeypatch):
    from hosted_access import access_status
    app, host, state, origin = hosted_app
    a = app.test_client()
    login(a, state, origin)
    row = family(host)
    assert access_status(row, row['trial_until'] - .001)['active']
    assert not access_status(row, row['trial_until'])['active']
    # Exercise the actual HTTP guard at both sides of the boundary, without
    # advancing the separate server-session datetime clock by a week.
    boundary = time.time() + 10
    with host.connect() as conn:
        conn.execute('UPDATE families SET trial_until=?', (boundary,))
    monkeypatch.setattr(time, 'time', lambda: boundary - .001)
    assert call(a, '/api/state', origin).status_code == 200
    monkeypatch.setattr(time, 'time', lambda: boundary)
    assert call(a, '/api/state', origin).status_code == 402
    assert call(a, '/billing', origin).status_code == 200
    row['paid_until'] = row['trial_until'] + 100
    assert access_status(row, row['trial_until'])['kind'] == 'paid'
    row['free_access'] = 1
    row['paid_until'] = 0
    assert access_status(row, row['trial_until'])['kind'] == 'free'


def test_operator_grant_revoke_durable_scoped_and_webhook_independent(hosted_app):
    app, host, state, origin = hosted_app
    a, b = app.test_client(), app.test_client()
    login(a, state, origin)
    login(b, state, origin, 'parent-b')
    expire(host)
    expire(host, 'parent-b')
    fa, fb = family(host), family(host, 'parent-b')
    assert fa['id'] in operator(host, 'list').stdout
    assert 'parent-a' in operator(host, 'show', fa['id']).stdout
    operator(host, 'grant', fa['id'], '--reason', 'Cannot pay; synthetic test')
    assert family(host)['free_access'] == 1  # Fresh process persisted it.
    assert call(a, '/api/state', origin).status_code == 200
    assert call(b, '/api/state?family=' + fa['id'], origin).status_code == 402
    assert b'Free access' in call(a, '/billing', origin).data
    # A subscription webhook cannot erase a grant or restart a trial.
    post(a, '/billing/checkout', origin)
    assert signed_event(a, origin).status_code == 200
    assert family(host)['free_access'] == 1
    assert family(host)['trial_until'] == fa['trial_until']
    assert call(a, '/api/state', origin).status_code == 200
    operator(host, 'revoke', fa['id'], '--reason', 'Synthetic revocation')
    assert call(a, '/api/state', origin).status_code == 402
    assert family(host, 'parent-b') == fb
    with host.connect() as conn:
        events = [dict(row) for row in conn.execute('SELECT * FROM access_audit ORDER BY id')]
    assert [e['action'] for e in events] == ['grant', 'revoke']
    assert all(e['family'] == fa['id'] and e['reason'] for e in events)
    operator(host, 'grant', 'not-a-family', '--reason', 'test', success=False)
    operator(host, 'grant', 'f' * 32, '--reason', 'test', success=False)
    operator(host, 'grant', fa['id'], success=False)
    assert call(a, '/billing', origin).status_code == 200
    # Revoking free access does not cancel paid access.
    state['subscriptions']['cus_1'] = [subscription()]
    signed_event(a, origin, 'evt_paid_after_grant')
    operator(host, 'grant', fa['id'], '--reason', 'test')
    operator(host, 'revoke', fa['id'], '--reason', 'test')
    assert call(a, '/api/state', origin).status_code == 200


def test_operator_audit_failure_rolls_back_and_wrong_path_never_creates(hosted_app, tmp_path):
    app, host, state, origin = hosted_app
    a = app.test_client()
    login(a, state, origin)
    expire(host)
    row = family(host)
    with host.connect() as conn:
        conn.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON access_audit BEGIN SELECT RAISE(ABORT, 'Synthetic audit failure'); END")
    operator(host, 'grant', row['id'], '--reason', 'Synthetic audit failure', success=False)
    assert family(host)['free_access'] == 0
    assert call(a, '/api/state', origin).status_code == 402
    missing = tmp_path / 'does-not-exist'
    result = subprocess.run([sys.executable, 'hosted_access.py', '--data', str(missing), 'list'],
                            capture_output=True, timeout=5)
    assert result.returncode != 0 and not missing.exists()


def test_migration_interruption_rolls_back_then_restart_completes(tmp_path):
    from hosted_access import migrate
    class Interrupted(sqlite3.Connection):
        fail = True
        def execute(self, sql, *args):
            if self.fail and sql.startswith('UPDATE families SET trial_started='):
                raise sqlite3.OperationalError('Synthetic interruption after DDL')
            return super().execute(sql, *args)
    path = tmp_path / 'directory.sqlite'
    conn = sqlite3.connect(path, factory=Interrupted)
    try:
        with conn:
            conn.execute('CREATE TABLE families (id TEXT PRIMARY KEY, paid_until REAL)')
            conn.execute('INSERT INTO families VALUES (?, ?)', ('a'*32, 12345))
        with pytest.raises(sqlite3.OperationalError):
            with conn:
                migrate(conn, now=100)
        assert [r[1] for r in conn.execute('PRAGMA table_info(families)')] == ['id', 'paid_until']
        assert conn.execute('SELECT * FROM families').fetchall() == [('a'*32, 12345)]
    finally:
        conn.close()
    with sqlite3.connect(path) as restarted:
        migrate(restarted, now=200)
        assert restarted.execute('SELECT paid_until, trial_started, trial_until, free_access FROM families').fetchone() == (12345, 200, 200 + 31 * 86400, 0)


def test_legacy_migration_preserves_paid_and_bank_data_and_runs_once(tmp_path):
    import db
    from hosted_access import migrate, access_status
    path = tmp_path / 'legacy.sqlite'
    bank_path = tmp_path / ('family-' + 'a'*32 + '.sqlite')
    db.init_db(path=str(bank_path))
    with sqlite3.connect(bank_path) as bank:
        bank.execute('INSERT INTO kids(name, wallet_cents, allowance_anchor_ts, savings_accrued_ts, created_ts) VALUES (?, ?, ?, ?, ?)',
                     ('Legacy Nickname', 12345, 1000, 1000, 1000))
        bank.execute('INSERT INTO transactions(kid_id, ts, kind, amount_cents, note) VALUES (1, 1000, ?, 12345, ?)',
                     ('gift', 'Synthetic legacy ledger'))
    bank_before = bank_path.read_bytes()
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE families (id TEXT PRIMARY KEY, issuer TEXT, subject TEXT, customer TEXT, paid_until REAL)')
        conn.execute('INSERT INTO families VALUES (?, ?, ?, ?, ?)', ('a'*32, 'https://issuer.test', 'old', 'cus_old', 9999999999))
        conn.commit()
        migrate(conn, now=1000)
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute('SELECT * FROM families').fetchone())
        assert row['paid_until'] == 9999999999 and row['customer'] == 'cus_old'
        assert row['trial_started'] == 1000 and row['trial_until'] == 1000 + 31 * 86400
        assert access_status(row, 1000)['kind'] == 'paid'
        conn.commit()
        migrate(conn, now=2000)
        assert dict(conn.execute('SELECT * FROM families').fetchone()) == row
    assert bank_path.read_bytes() == bank_before


@pytest.mark.parametrize('support', ['mailto:owner@example.test', 'https://support.example.test/help'])
def test_contact_configuration(hosted_app, monkeypatch, support):
    app, host, state, origin = hosted_app
    monkeypatch.setenv('BOD_SUPPORT_URL', support)
    # Config is validated at startup; this pure helper is also used by startup.
    from hosted_access import support_destination
    host.support_url = support_destination()
    a = app.test_client()
    login(a, state, origin)
    assert support.encode() in call(a, '/', origin).data
    expire(host)
    page = call(a, '/billing', origin).data
    assert support.encode() in page and b'Contact about free access' in page
    assert family(host)['id'].encode() in page


@pytest.mark.parametrize('value', ['javascript:alert(1)', 'http://insecure.test', 'mailto:bad\r\n@example.test', '//example.test', 'https://user:pass@example.test'])
def test_invalid_support_destination_rejected(monkeypatch, value):
    from hosted_access import support_destination
    monkeypatch.setenv('BOD_SUPPORT_URL', value)
    with pytest.raises(ValueError):
        support_destination()


def test_trial_and_operator_access_on_real_family_scoped_websockets(hosted_app):
    from test_hosted_realtime import network, connect, cookie, invalidation, quiet
    from websocket import WebSocketConnectionClosedException, WebSocketBadStatusException
    app, host, state, origin = hosted_app
    a, b = app.test_client(), app.test_client()
    login(a, state, origin)
    login(b, state, origin, 'parent-b')
    sockets = []
    def closed(ws):
        ws.settimeout(3)
        try:
            assert ws.recv() == ''
        except WebSocketConnectionClosedException:
            pass
    with network(app) as base:
        try:
            for client in (a, a, b):
                sockets.append(connect(base, origin, cookie(client)))
                invalidation(sockets[-1])
            one, two, other = sockets
            assert post(a, '/api/admin/kid', origin, json={'name': 'Trial A'}).json['ok']
            invalidation(one)
            invalidation(two)
            quiet(other)
            # Natural time boundary closes idle sockets, without a bank mutation.
            with host.connect() as conn:
                conn.execute("UPDATE families SET trial_until=? WHERE subject='parent-a'", (time.time() + .6,))
            closed(one)
            closed(two)
            with pytest.raises(WebSocketBadStatusException) as error:
                connect(base, origin, cookie(a))
            assert error.value.status_code == 402
            quiet(other)
            operator(host, 'grant', family(host)['id'], '--reason', 'Synthetic no-cost access')
            free = connect(base, origin, cookie(a))
            sockets.append(free)
            invalidation(free)
            assert call(a, '/api/state', origin).json['kids'][0]['name'] == 'Trial A'
            operator(host, 'revoke', family(host)['id'], '--reason', 'Synthetic end of access')
            closed(free)
            assert call(a, '/api/state', origin).status_code == 402
            assert post(b, '/api/admin/kid', origin, json={'name': 'Trial B'}).json['ok']
            invalidation(other)
            assert call(b, '/api/state', origin).json['kids'][0]['name'] == 'Trial B'
        finally:
            for ws in sockets:
                ws.close()
