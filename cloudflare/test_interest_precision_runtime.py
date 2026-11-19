"""Actual workerd/SQLite regression against an independent Fraction oracle."""
from fractions import Fraction as F
import math
import sqlite3
import time
import pytest
from test_runtime import runtime, state, snapshot


@pytest.mark.parametrize('pct', [1., math.nextafter(2., 0.), 2., math.nextafter(2., math.inf)])
def test_exact_periods_and_legacy_carry_restart(runtime, pct):
    cookie, csrf = runtime.parent_guest()
    path = runtime.database(cookie)
    # Use real parent settings ingress for the finite decimal rate.
    code, result, _ = runtime.parent_call('/api/admin/rates', cookie=cookie, csrf=csrf,
        body={'savings_weekly_pct': pct, 'savings_period_days': 1, 'auto_interest_enabled': True})
    assert code == 200 and result['ok'], result
    runtime.stop()
    anchor = int(time.time())-20*86400
    with sqlite3.connect(path) as c:
        # Existing REAL data without a rational carry is imported, not repriced.
        c.execute('UPDATE kids SET savings_cents=25,savings_interest_carry=0,savings_interest_exact=NULL,savings_accrued_ts=?,auto_interest_due_ts=?', (anchor, anchor+86400))
        c.execute("DELETE FROM transactions WHERE kind='interest'")
    runtime.start()
    principal, carry, ledger = 25, F(0), []
    for _ in range(20):
        earned = carry+principal*F(str(pct))/100
        paid = max(0, round(earned))
        principal += paid
        carry = earned-paid
        if paid:
            ledger.append(paid)
    assert state(runtime, cookie)['kids'][0]['savings_cents'] == principal
    with sqlite3.connect(path) as c:
        assert [r[0] for r in c.execute("SELECT amount_cents FROM transactions WHERE kind='interest' ORDER BY id")] == ledger
        raw, due = c.execute('SELECT savings_interest_exact,auto_interest_due_ts FROM kids').fetchone()
        assert F(raw.split('@')[0]) == carry
        assert due == anchor+21*86400
    before = snapshot(path)
    runtime.stop(); runtime.start()
    assert state(runtime, cookie)['kids'][0]['savings_cents'] == principal
    assert snapshot(path) == before
