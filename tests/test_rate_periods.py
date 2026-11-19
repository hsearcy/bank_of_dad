"""Disposable SQLite coverage for editable terms and non-retroactive accrual."""
import sqlite3
import threading
from unittest.mock import patch

import pytest

import bank
import db


@pytest.fixture
def family(tmp_path, monkeypatch):
    path = tmp_path / 'family.sqlite'
    monkeypatch.setattr(db, 'DB_PATH', str(path))
    db.init_db(path=path)
    conn = db.get_conn(path)
    with patch('bank.time.time', return_value=2_000_000_000):
        bank.add_kid(conn, 'Synthetic')
        conn.execute('UPDATE kids SET wallet_cents=10000, savings_cents=101, savings_accrued_ts=?',
                     (2_000_000_000 - 3 * 86400,))
        conn.commit()
        yield conn
    conn.close()


def dump(conn):
    return list(conn.iterdump())


def kid(conn):
    return conn.execute('SELECT * FROM kids WHERE id=1').fetchone()


def test_unchanged_save_is_exact_noop_and_does_not_round_rates(family):
    import app
    db.set_setting(family, 'savings_weekly_pct', 4.123456789)
    family.commit()
    before = dump(family)
    cfg = app.cfg(family)
    assert cfg['savings_weekly_pct'] == 4.123456789
    bank.set_rates(family, cfg['savings_weekly_pct'], {b['key']: b['return_pct'] for b in cfg['bonds']},
                   cfg['savings_period_days'], {b['key']: b['days'] for b in cfg['bonds']})
    assert dump(family) == before


def test_period_and_rate_changes_keep_fractional_earnings_and_balances(family):
    before = dict(kid(family))
    expected = 101 * .04 * 3 / 7
    pending = bank.pending_interest(kid(family), bank.now_ts(family), bank.savings_weekly_rate(family))
    bank.set_rates(family, 6, savings_days=2)
    after = kid(family)
    assert after['wallet_cents'] == before['wallet_cents']
    assert after['savings_cents'] == before['savings_cents']
    assert after['savings_interest_carry'] == pytest.approx(expected)
    assert bank.pending_interest(after, bank.now_ts(family), bank.savings_weekly_rate(family)) == pending
    assert family.execute('SELECT count(*) FROM transactions').fetchone()[0] == 0
    bank.fast_forward(family, 2)
    expected += 101 * .06
    assert bank.pending_interest(kid(family), bank.now_ts(family), bank.savings_weekly_rate(family)) == round(expected)
    bank.set_rates(family, 0, savings_days=3650)
    assert kid(family)['savings_interest_carry'] == pytest.approx(expected)
    bank.fast_forward(family, 10)
    assert bank.collect_rewards(family, 1, 'interest') == round(expected)
    assert kid(family)['savings_cents'] == 101 + round(expected)
    assert kid(family)['savings_interest_carry'] == 0
    assert bank.collect_rewards(family, 1, 'interest') == 0
    assert family.execute("SELECT amount_cents FROM transactions WHERE kind='interest'").fetchall()[0][0] == round(expected)


@pytest.mark.parametrize('action', ['save', 'withdraw', 'allowance'])
def test_savings_actions_consume_carry_once_but_allowance_does_not(family, action):
    bank.set_rates(family, savings_days=14)
    earned = round(101 * .04 * 3 / 7)
    if action == 'save':
        bank.move_to_savings(family, 1, 10)
        assert kid(family)['savings_cents'] == 111 + earned
    elif action == 'withdraw':
        bank.withdraw_savings(family, 1, 10)
        assert kid(family)['savings_cents'] == 91 + earned
    else:
        bank.collect_rewards(family, 1, 'allowance')
        assert kid(family)['savings_interest_carry'] > 0
        return
    assert kid(family)['savings_interest_carry'] == 0
    assert bank.pending_interest(kid(family), bank.now_ts(family), bank.savings_weekly_rate(family)) == 0


def test_existing_and_new_contracts_have_their_own_duration_and_payout(family):
    bank.buy_bond(family, 1, '1mo', 100)
    old = dict(family.execute('SELECT * FROM bonds WHERE id=1').fetchone())
    bank.set_rates(family, returns={'1mo': 50}, bond_period_days={'1mo': 2, '3mo': 3650})
    assert dict(family.execute('SELECT * FROM bonds WHERE id=1').fetchone()) == old
    bank.buy_bond(family, 1, '1mo', 100)
    new = family.execute('SELECT * FROM bonds WHERE id=2').fetchone()
    assert new['mature_ts'] - new['buy_ts'] == 2 * 86400
    assert new['payout_cents'] == 150
    states = bank.all_kids_state(family)[0]['bonds']
    assert {b['term'] for b in states} == {'1 Month', '2 Days'}
    bank.fast_forward(family, 2)
    assert bank.collect_rewards(family, 1, 'invest') == 50
    assert family.execute('SELECT collected FROM bonds WHERE id=1').fetchone()[0] == 0
    bank.fast_forward(family, 28)
    assert bank.collect_rewards(family, 1, 'invest') == 25


@pytest.mark.parametrize('bad', [-1, 0, .1, 3651, True, '7', [], {}, float('inf'), float('nan')])
@pytest.mark.parametrize('field', ['savings_days', 'bond_period_days'])
def test_invalid_periods_leave_everything_unchanged(family, bad, field):
    before = dump(family)
    args = {field: {'1mo': bad} if field == 'bond_period_days' else bad}
    with pytest.raises(ValueError):
        bank.set_rates(family, 5, returns={'3mo': 30}, **args)
    assert dump(family) == before


@pytest.mark.parametrize('bad', [-1, True, '2', [], {}, float('inf'), float('nan')])
def test_invalid_percentages_and_unknown_terms_are_atomic(family, bad):
    before = dump(family)
    with pytest.raises(ValueError):
        bank.set_rates(family, 5, {'1mo': bad}, 2)
    with pytest.raises(ValueError):
        bank.set_rates(family, 5, {'missing': 20}, 2)
    assert dump(family) == before


@pytest.mark.parametrize('days', [1, 7, 14, 30, 90, 3650])
def test_boundary_roundtrips(family, days):
    bank.set_rates(family, 0, {'1mo': 0, '3mo': 12.123456789}, days, {'1mo': days, '3mo': days})
    assert bank.savings_period_days(family) == days
    assert bank.bond_days(family, '1mo') == days
    assert bank.bond_return_pct(family, '3mo') == 12.123456789
    before = dump(family)
    bank.set_rates(family, 0, {'1mo': 0, '3mo': 12.123456789}, days, {'1mo': days, '3mo': days})
    assert dump(family) == before


def test_sql_failure_rolls_back_boundaries_and_settings_and_caller_work(family):
    family.execute("CREATE TRIGGER fail_rates BEFORE INSERT ON settings BEGIN SELECT RAISE(ABORT, 'injected'); END")
    family.commit()
    before = dump(family)
    with pytest.raises(sqlite3.IntegrityError):
        bank.set_rates(family, 6, savings_days=2)
    assert dump(family) == before
    family.execute('DROP TRIGGER fail_rates')
    family.execute('BEGIN IMMEDIATE')
    family.execute("UPDATE kids SET name='Caller-owned'")
    bank.set_rates(family, savings_days=2)
    assert family.in_transaction
    family.rollback()
    assert kid(family)['name'] == 'Synthetic'
    assert bank.savings_period_days(family) == 7


def test_future_accrual_anchor_does_not_move_backwards(family):
    future = bank.now_ts(family) + 86400
    family.execute('UPDATE kids SET savings_accrued_ts=?', (future,))
    family.commit()
    bank.set_rates(family, savings_days=1)
    assert kid(family)['savings_accrued_ts'] == future
    assert kid(family)['savings_interest_carry'] == 0


@pytest.mark.parametrize('action', ['interest', 'all', 'save', 'withdraw'])
@pytest.mark.parametrize('principal', [10000, 101])
def test_consuming_carry_after_clock_rewind_does_not_reopen_earned_time(family, action, principal):
    start = bank.now_ts(family)
    family.execute('UPDATE kids SET savings_cents=?, savings_accrued_ts=?, allowance_enabled=0',
                   (principal, start))
    family.commit()
    bank.fast_forward(family, 7)
    boundary = bank.now_ts(family)
    bank.set_rates(family, 4, savings_days=1)
    exact = principal * .04
    assert kid(family)['savings_interest_carry'] == pytest.approx(exact)
    bank.fast_forward(family, -7)
    assert bank.now_ts(family) == start
    if action in ('interest', 'all'):
        assert bank.collect_rewards(family, 1, action) == round(exact)
        expected = principal + round(exact)
        assert family.execute("SELECT kind,amount_cents FROM transactions").fetchall()[0][:] == ('interest', round(exact))
    elif action == 'save':
        assert bank.move_to_savings(family, 1, 100)[0]
        expected = principal + round(exact) + 100
    else:
        assert bank.withdraw_savings(family, 1, 100)[0]
        expected = principal + round(exact) - 100
    assert kid(family)['savings_cents'] == expected
    assert kid(family)['savings_interest_carry'] == 0  # existing collection rounding, not fractions recycled
    assert kid(family)['savings_accrued_ts'] == boundary
    bank.fast_forward(family, 7)
    assert bank.pending_interest(kid(family), bank.now_ts(family), bank.savings_weekly_rate(family)) == 0
    before = dump(family)
    assert bank.collect_rewards(family, 1, 'interest') == 0
    assert dump(family) == before
    bank.fast_forward(family, 1)
    earned_forward = round(expected * .04)
    assert bank.collect_rewards(family, 1, 'interest') == earned_forward
    assert kid(family)['savings_accrued_ts'] == (bank.now_ts(family) if earned_forward else boundary)


@pytest.mark.parametrize('action', ['save', 'withdraw'])
def test_transfer_without_carry_preserves_a_future_anchor(family, action):
    future = bank.now_ts(family) + 86400
    family.execute('UPDATE kids SET savings_accrued_ts=?', (future,))
    family.commit()
    fn = bank.move_to_savings if action == 'save' else bank.withdraw_savings
    assert fn(family, 1, 10)[0]
    assert kid(family)['savings_accrued_ts'] == future
    bank.fast_forward(family, 1)
    assert bank.pending_interest(kid(family), bank.now_ts(family), bank.savings_weekly_rate(family)) == 0


def test_existing_schema_migration_preserves_every_old_value(tmp_path):
    path = tmp_path / 'legacy.sqlite'
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA.replace('    savings_interest_carry  REAL NOT NULL DEFAULT 0,\n', ''))
        conn.execute("INSERT INTO kids(name,wallet_cents,savings_cents,allowance_anchor_ts,savings_accrued_ts,created_ts) VALUES('Existing',123,456,100,100,100)")
        conn.execute("INSERT INTO bonds(kid_id,term_key,principal_cents,payout_cents,buy_ts,mature_ts) VALUES(1,'1mo',50,63,100,2592100)")
        conn.execute("INSERT INTO settings VALUES('savings_weekly_pct','4.123456789')")
        conn.commit()
        old = {t: [dict(r) for r in conn.execute('SELECT * FROM ' + t)] for t in ('kids', 'bonds', 'settings', 'transactions', 'history', 'sqlite_sequence')}
    db.init_db(path=path)
    with db.get_conn(path) as conn:
        for table, rows in old.items():
            actual = [dict(r) for r in conn.execute('SELECT * FROM ' + table)]
            if table == 'kids':
                assert actual[0].pop('savings_interest_carry') == 0
            assert actual == rows
        before = dump(conn)
        db._migrate(conn)
        assert dump(conn) == before
        assert bank.savings_period_days(conn) == 7
        assert bank.bond_days(conn, '1mo') == 30


def test_api_pin_validation_and_precision(family):
    import app
    client = app.app.test_client()
    bank.set_dad_pin(family, '1234')
    before = dump(family)
    assert client.post('/api/admin/rates', json={'savings_period_days': 1}).status_code == 403
    assert dump(family) == before
    headers = {'X-Dad-Pin': '1234'}
    for body in ({'savings_period_days': -1}, {'bond_period_days': {'1mo': 1.5}}, [], {'returns': []}):
        response = client.post('/api/admin/rates', json=body, headers=headers)
        assert response.status_code == 400
        assert not response.json['ok']
        assert dump(family) == before
    response = client.post('/api/admin/rates', json={'savings_weekly_pct': 4.123456789, 'savings_period_days': 2, 'bond_period_days': {'1mo': 3}}, headers=headers)
    assert response.json['ok']
    cfg = client.get('/api/state').json['config']
    assert cfg['savings_weekly_pct'] == 4.123456789
    assert cfg['savings_period_days'] == 2
    assert cfg['bonds'][0]['days'] == 3


def test_request_owned_sqlite_transaction_commits_terms(family, monkeypatch):
    import app
    from flask import g
    # Exercise the retained hosted SQLite request ownership without any provider.
    bank.set_dad_pin(family, '1234')
    monkeypatch.setenv('BANK_OF_DAD_MODE', 'hosted')
    with app.app.test_request_context('/api/admin/rates', method='POST', json={'savings_period_days': 14}, headers={'X-Dad-Pin': '1234'}):
        g.bank_db_path = db.DB_PATH
        response = app.api_set_rates()
        assert response.json['ok']
    assert bank.savings_period_days(family) == 14


def test_collector_waits_for_period_change_before_reading(family, monkeypatch):
    import app
    attempted, finished = threading.Event(), threading.Event()
    original_connect = sqlite3.connect
    result, errors = [], []

    def traced_connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        conn.set_trace_callback(lambda sql: attempted.set() if sql == 'BEGIN IMMEDIATE' else None)
        return conn

    # A real competing request must reach BEGIN IMMEDIATE while this writer owns
    # the lock, not merely start its thread. It cannot read the old kid anchor.
    family.execute('BEGIN IMMEDIATE')
    monkeypatch.setattr(db.sqlite3, 'connect', traced_connect)

    def collect():
        try:
            with app.app.test_client() as client:
                result.append(client.post('/api/collect', json={'kid_id': 1, 'kind': 'interest'}).json)
        except Exception as error:
            errors.append(error)
        finally:
            finished.set()

    worker = threading.Thread(target=collect)
    worker.start()
    try:
        assert attempted.wait(5)
        assert not finished.is_set()
        bank.set_rates(family, 100, savings_days=1)
        family.commit()
    finally:
        family.rollback()
        worker.join(timeout=10)
    assert not worker.is_alive() and not errors
    assert result == [{'ok': True, 'collected_cents': 2}]
    assert kid(family)['savings_cents'] == 103
    assert kid(family)['savings_interest_carry'] == 0
