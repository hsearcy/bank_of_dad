"""Retained pre-upgrade family files and real persisted sessions, no providers."""
import hashlib
import importlib
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import bank
import db
from hosted import digest
from test_hosted import hosted_app, call, post


TABLES = ('kids', 'bonds', 'transactions', 'history', 'settings', 'sqlite_sequence')


def rows(path):
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return {table: [dict(row) for row in conn.execute('SELECT * FROM ' + table)]
                for table in TABLES}


def checksum(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def legacy_family(host, now):
    family = secrets.token_hex(16)
    path = host.family_path(family)
    # Create the OLD schema directly: never initialize a modern bank and erase a
    # column, and never log in to accidentally migrate the fixture before testing.
    with sqlite3.connect(path) as conn:
        conn.executescript(db.SCHEMA.replace('    savings_interest_carry  REAL NOT NULL DEFAULT 0,\n', ''))
        conn.execute("INSERT INTO kids(id,name,wallet_cents,savings_cents,allowance_enabled,allowance_anchor_ts,savings_accrued_ts,created_ts) VALUES(7,'Retained',5000,10000,0,?,?,?)",
                     (now - 7 * 86400, now - 7 * 86400, now - 30 * 86400))
        conn.execute("INSERT INTO bonds(kid_id,term_key,principal_cents,payout_cents,buy_ts,mature_ts) VALUES(7,'1mo',100,125,?,?)",
                     (now - 10 * 86400, now + 20 * 86400))
        conn.execute("INSERT INTO transactions(kid_id,ts,kind,amount_cents,note,bond_id) VALUES(7,?,'invest',100,'Retained investment',1)", (now - 10 * 86400,))
        conn.execute("INSERT INTO history(kid_id,ts,networth_cents,wallet_cents,savings_cents,invest_cents) VALUES(7,?,15100,5000,10000,100)", (now - 10 * 86400,))
        conn.execute("INSERT INTO settings VALUES('savings_weekly_pct','4')")
    return family, path


@pytest.mark.parametrize('role', ['guest', 'saved'])
@pytest.mark.parametrize('first', ['state', 'rates', 'collect', 'save', 'withdraw'])
def test_retained_session_migrates_only_on_authorized_bank_open(hosted_app, role, first):
    app, host, provider, origin = hosted_app
    now = time.time()
    family, path = legacy_family(host, now)
    capability = secrets.token_urlsafe(32)
    with host.connect() as conn:
        conn.execute('INSERT INTO families(id,issuer,subject,trial_started,trial_until,guest_hash,trial_policy) VALUES(?,?,?,?,?,?,1)',
                     (family, 'urn:bank-of-dad:guest' if role == 'guest' else host.issuer,
                      family, now - 86400, now + 86400, digest(capability) if role == 'guest' else None))
        association = dict(conn.execute('SELECT * FROM families WHERE id=?', (family,)).fetchone())
    client = app.test_client()
    with client.session_transaction(base_url=origin) as session:
        session['family'] = family
        session['csrf'] = secrets.token_urlsafe(32)
        if role == 'guest':
            session['guest'] = capability
        original_session = dict(session)
    cookie = client.get_cookie('__Host-bod-session', domain='bank.example.test').value
    before, before_hash = rows(path), checksum(path)

    # An actual new Flask application must not eagerly open or change family
    # files at startup; preserve the old server-side session and opaque cookie.
    import app as module
    module = importlib.reload(module)
    app = module.app
    app.testing = True
    host = app.extensions['bod_hosted']
    client = app.test_client()
    client.set_cookie('__Host-bod-session', cookie, domain='bank.example.test')
    assert checksum(path) == before_hash
    assert not Path(db.DB_PATH).exists()
    assert call(app.test_client(), '/api/state', origin).status_code == 401
    assert call(client, '/api/admin/rates', origin, method='POST', json={'savings_period_days': 1}).status_code == 403
    assert call(client, '/api/admin/rates', origin, method='POST', json={'savings_period_days': 1},
                headers={'X-CSRF-Token': original_session['csrf'], 'Origin': 'https://foreign.invalid'}).status_code == 403
    assert checksum(path) == before_hash

    with patch('bank.time.time', return_value=now):
        if first == 'state':
            response = call(client, '/api/state', origin)
            assert response.status_code == 200
            kid = response.json['kids'][0]
            assert (kid['id'], kid['wallet_cents'], kid['savings_cents'], kid['pending_interest_cents']) == (7, 5000, 10000, 400)
        elif first == 'rates':
            assert post(client, '/api/admin/rates', origin, json={'savings_period_days': 1}).json == {'ok': True}
        elif first == 'collect':
            assert post(client, '/api/collect', origin, json={'kid_id': 7, 'kind': 'interest'}).json == {'ok': True, 'collected_cents': 400}
        else:
            assert post(client, '/api/' + first, origin, json={'kid_id': 7, 'amount_cents': 100}).json['ok']
        after = rows(path)
        assert after['bonds'] == before['bonds']
        if first in ('state', 'rates'):
            assert after['transactions'] == before['transactions']
            assert after['history'] == before['history']
            restored = dict(after['kids'][0])
            carry = restored.pop('savings_interest_carry')
            if first == 'rates':
                assert carry == 400
                assert restored.pop('savings_accrued_ts') == now
                expected = dict(before['kids'][0])
                expected.pop('savings_accrued_ts')
                assert restored == expected
            else:
                assert carry == 0
                assert restored == before['kids'][0]
                assert after['settings'] == before['settings']
                assert after['sqlite_sequence'] == before['sqlite_sequence']
        else:
            expected = {'collect': (5000, 10400, 'interest', 400),
                        'save': (4900, 10500, 'save', 100),
                        'withdraw': (5100, 10300, 'withdraw', 100)}[first]
            assert (after['kids'][0]['wallet_cents'], after['kids'][0]['savings_cents']) == expected[:2]
            assert len(after['transactions']) == len(before['transactions']) + 1
            assert (after['transactions'][-1]['kind'], after['transactions'][-1]['amount_cents']) == expected[2:]
            assert len(after['history']) == len(before['history']) + 1
        # Current-schema reads must be a true no-op, not reseeding or rewriting.
        stable = checksum(path)
        assert call(client, '/api/state', origin).status_code == 200
        assert checksum(path) == stable
    assert client.get_cookie('__Host-bod-session', domain='bank.example.test').value == cookie
    with client.session_transaction(base_url=origin) as session:
        assert dict(session) == original_session
    with host.connect() as conn:
        assert dict(conn.execute('SELECT * FROM families WHERE id=?', (family,)).fetchone()) == association
    assert provider['token_calls'] == 0
    assert not Path(db.DB_PATH).exists()


@pytest.mark.parametrize('scenario', ['rollback', 'concurrent'])
def test_first_authorized_migration_is_atomic_and_serialized(hosted_app, monkeypatch, scenario):
    app, host, _, origin = hosted_app
    family, path = legacy_family(host, time.time())
    with host.connect() as conn:
        conn.execute('INSERT INTO families(id,issuer,subject,trial_until) VALUES(?,?,?,?)',
                     (family, host.issuer, family, time.time() + 86400))
    client = app.test_client()
    with client.session_transaction(base_url=origin) as session:
        session['family'] = family
        session['csrf'] = secrets.token_urlsafe(32)
    original_migrate = db._migrate
    before = checksum(path)
    if scenario == 'rollback':
        def fail_after_ddl(conn):
            assert original_migrate(conn)
            raise sqlite3.OperationalError('injected after DDL')

        monkeypatch.setattr(db, '_migrate', fail_after_ddl)
        with pytest.raises(sqlite3.OperationalError, match='injected after DDL'):
            call(client, '/api/state', origin)
        assert checksum(path) == before
        # A separate connection can immediately reacquire the failed writer lock.
        with sqlite3.connect(path, timeout=0) as conn:
            conn.execute('BEGIN IMMEDIATE')
            assert 'savings_interest_carry' not in {r[1] for r in conn.execute('PRAGMA table_info(kids)')}
        monkeypatch.setattr(db, '_migrate', original_migrate)
        assert call(client, '/api/state', origin).status_code == 200
        return

    migrated, release, contended = threading.Event(), threading.Event(), threading.Event()
    outcomes, responses, errors = [], [], []
    original_connect = sqlite3.connect

    def trace_connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        if Path(path).name in str(args[0]):
            def trace(sql):
                if sql == 'BEGIN IMMEDIATE' and threading.current_thread().name == 'second-upgrader':
                    contended.set()
            conn.set_trace_callback(trace)
        return conn

    def pause_migrate(conn):
        result = original_migrate(conn)
        outcomes.append(result)
        if result:
            migrated.set()
            assert release.wait(5)
        return result

    monkeypatch.setattr(db.sqlite3, 'connect', trace_connect)
    monkeypatch.setattr(db, '_migrate', pause_migrate)
    cookie = client.get_cookie('__Host-bod-session', domain='bank.example.test').value

    def read():
        try:
            reader = app.test_client()
            reader.set_cookie('__Host-bod-session', cookie, domain='bank.example.test')
            responses.append(call(reader, '/api/state', origin))
        except Exception as exc:
            errors.append(exc)

    first = threading.Thread(target=read, name='first-upgrader')
    second = threading.Thread(target=read, name='second-upgrader')
    first.start()
    try:
        assert migrated.wait(5)
        second.start()
        assert contended.wait(5)  # observed second BEGIN while first owns DDL
        assert outcomes == [True]
        assert not responses
    finally:
        release.set()
        first.join(10)
        if second.ident is not None:
            second.join(10)
    assert not first.is_alive() and not second.is_alive()
    assert not errors
    assert outcomes == [True, False]
    assert len(responses) == 2 and all(r.status_code == 200 for r in responses)
    assert all(r.json['kids'][0]['savings_cents'] == 10000 for r in responses)


def test_denied_or_missing_bank_never_creates_family_file(hosted_app):
    app, host, _, origin = hosted_app
    anonymous = app.test_client()
    assert call(anonymous, '/api/state', origin).status_code == 401
    assert not list(host.root.glob('family-*.sqlite'))
    family = secrets.token_hex(16)
    with host.connect() as conn:
        conn.execute('INSERT INTO families(id,issuer,subject,trial_until) VALUES(?,?,?,?)',
                     (family, host.issuer, family, time.time() + 86400))
    with anonymous.session_transaction(base_url=origin) as session:
        session['family'] = family
    with pytest.raises(sqlite3.OperationalError, match='unable to open database file'):
        call(anonymous, '/api/state', origin)
    assert not Path(host.family_path(family)).exists()
    assert not Path(db.DB_PATH).exists()
