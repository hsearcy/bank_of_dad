"""Automatic savings: disposable genuine SQLite, fixed domain clock."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import sqlite3
import pytest
import bank
import db

NOW = 2_000_000_000
DAY = 86400

@pytest.fixture
def family(tmp_path, monkeypatch):
    path = tmp_path / 'auto.sqlite'
    monkeypatch.setattr(db, 'DB_PATH', str(path))
    db.init_db(path=path)
    c = db.get_conn(path)
    with patch('bank.time.time', return_value=NOW):
        bank.add_kid(c, 'Synthetic')
        c.execute('UPDATE kids SET savings_cents=10000, wallet_cents=10000, savings_accrued_ts=?', (NOW-7*DAY,))
        c.commit()
        yield c
    c.close()

def row(c):
    return c.execute('SELECT * FROM kids WHERE id=1').fetchone()

def test_enable_pending_first_due_and_no_refresh_compounding(family):
    assert not bank.auto_interest_enabled(family)
    bank.set_auto_interest(family, True)
    assert row(family)['savings_cents'] == 10400
    assert row(family)['auto_interest_due_ts'] == NOW+7*DAY
    before = list(family.iterdump())
    bank.set_auto_interest(family, True)
    for _ in range(5): bank.all_kids_state(family)
    assert list(family.iterdump()) == before
    bank.fast_forward(family, 6)
    assert bank.all_kids_state(family)[0]['savings_cents'] == 10400
    bank.fast_forward(family, 1)
    assert bank.all_kids_state(family)[0]['savings_cents'] == 10816
    assert bank.collect_rewards(family, 1, 'interest') == 0
    assert family.execute("SELECT count(*) FROM transactions WHERE kind='interest'").fetchone()[0] == 2

def test_disable_reenable_and_fraction_no_loss(family):
    family.execute('UPDATE kids SET savings_cents=1, savings_accrued_ts=?', (NOW,)); family.commit()
    bank.set_auto_interest(family, True)
    bank.fast_forward(family, 7)
    bank.all_kids_state(family)
    assert row(family)['savings_interest_carry'] == pytest.approx(.04)
    bank.set_auto_interest(family, False)
    bank.fast_forward(family, 7)
    bank.set_auto_interest(family, True)
    assert row(family)['savings_interest_carry'] == pytest.approx(.08)
    assert row(family)['auto_interest_due_ts'] == NOW+21*DAY

def test_period_rate_boundary_restarts_only_period(family):
    bank.set_auto_interest(family, True)
    bank.fast_forward(family, 3)
    bank.set_rates(family, 8)
    assert row(family)['auto_interest_due_ts'] == NOW+7*DAY
    earned = row(family)['savings_interest_carry']
    bank.set_rates(family, savings_days=2)
    assert row(family)['savings_interest_carry'] == earned
    assert row(family)['auto_interest_due_ts'] == NOW+5*DAY
    bank.fast_forward(family, 2)
    bank.all_kids_state(family)
    assert row(family)['savings_cents'] == 10400 + round(earned+10400*.08)

def test_restart_and_parallel_manual_due(family):
    path = family.execute('PRAGMA database_list').fetchone()[2]
    bank.set_auto_interest(family, True)
    bank.fast_forward(family, 28)
    family.close()
    def access(_):
        with patch('bank.time.time', return_value=NOW):
            c = db.get_conn(path)
            try:
                bank.collect_rewards(c, 1, 'interest')
                return bank.all_kids_state(c)[0]['savings_cents']
            finally: c.close()
    with ThreadPoolExecutor(4) as pool: values = list(pool.map(access, range(8)))
    expected = 10400
    for _ in range(4): expected += round(expected*.04)
    assert values == [expected]*8
    c = db.get_conn(path)
    assert c.execute("SELECT count(*) FROM transactions WHERE kind='interest'").fetchone()[0] == 5
    c.close()

def test_atomic_failure_and_caller_rollback(family):
    family.execute("CREATE TRIGGER reject_auto BEFORE INSERT ON transactions BEGIN SELECT RAISE(ABORT, 'injected'); END")
    family.commit()
    before = list(family.iterdump())
    with pytest.raises(sqlite3.IntegrityError): bank.set_auto_interest(family, True)
    assert list(family.iterdump()) == before
    family.execute('DROP TRIGGER reject_auto'); family.commit()
    family.execute('BEGIN IMMEDIATE')
    bank.set_auto_interest(family, True)
    family.rollback()
    assert not bank.auto_interest_enabled(family)
    assert row(family)['savings_cents'] == 10000

@pytest.mark.parametrize('days', [1, 7, 14, 30])
def test_realistic_31_day_periods(family, days):
    family.execute('UPDATE kids SET savings_accrued_ts=?', (NOW,)); family.commit()
    bank.set_rates(family, savings_days=days)
    bank.set_auto_interest(family, True)
    bank.fast_forward(family, 31)
    expected, carry = 10000, 0.0
    for _ in range(31//days):
        exact = expected*.04 + carry
        cents = round(exact)
        expected += cents
        carry = exact-cents
    assert bank.all_kids_state(family)[0]['savings_cents'] == expected
    assert row(family)['auto_interest_due_ts'] == NOW+(31//days+1)*days*DAY


def test_deposits_do_not_compound_between_due_dates(family):
    bank.set_auto_interest(family, True)
    due = row(family)['auto_interest_due_ts']
    bank.fast_forward(family, 3)
    bank.move_to_savings(family, 1, 1000)
    earned = 10400*.04*3/7
    assert row(family)['savings_interest_carry'] == pytest.approx(earned)
    assert row(family)['savings_cents'] == 11400
    bank.withdraw_savings(family, 1, 500)
    assert row(family)['savings_interest_carry'] == pytest.approx(earned)
    assert row(family)['auto_interest_due_ts'] == due
    bank.fast_forward(family, 4)
    assert bank.all_kids_state(family)[0]['savings_cents'] == 10900+round(earned+10900*.04*4/7)
    assert family.execute("SELECT count(*) FROM transactions WHERE kind='interest'").fetchone()[0] == 2


def test_bounded_catchup_commits_progress_before_mutations(family, monkeypatch):
    monkeypatch.setattr(bank, 'AUTO_BATCH', 2)
    bank.set_rates(family, savings_weekly_pct=0, savings_days=1)
    bank.set_auto_interest(family, True)
    bank.fast_forward(family, 6)
    with pytest.raises(bank.InterestCatchupPending): bank.all_kids_state(family)
    old_wallet = row(family)['wallet_cents']
    with pytest.raises(bank.InterestCatchupPending): bank.gift(family, 1, 100)
    assert row(family)['wallet_cents'] == old_wallet
    bank.gift(family, 1, 100)
    assert row(family)['wallet_cents'] == old_wallet+100
    assert row(family)['auto_interest_due_ts'] == NOW+7*DAY


def test_parent_pin_and_retained_legacy_schema(family, monkeypatch):
    import app
    bank.set_dad_pin(family, '1234')
    client = app.app.test_client()
    assert client.post('/api/admin/rates', json={'auto_interest_enabled':True}).status_code == 403
    assert not bank.auto_interest_enabled(family)
    assert client.post('/api/admin/rates', json={'auto_interest_enabled':True},headers={'X-Dad-Pin':'1234'}).json['ok']
    assert client.get('/api/state').json['config']['auto_interest_enabled']
    assert not client.get('/api/state').json['config']['auto_interest_background']


def test_rewind_and_manual_default(family):
    bank.fast_forward(family, 31)
    assert bank.all_kids_state(family)[0]['savings_cents'] == 10000
    bank.set_auto_interest(family, True)
    anchor = row(family)['savings_accrued_ts']
    bank.fast_forward(family, -40)
    bank.set_auto_interest(family, False)
    bank.set_auto_interest(family, True)
    assert row(family)['savings_accrued_ts'] == anchor
    bank.fast_forward(family, 40)
    assert bank.collect_rewards(family, 1, 'interest') == 0
