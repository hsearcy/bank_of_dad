"""Independent rational oracle: never derive expected values from bank helpers."""
from fractions import Fraction as F
import math
import sqlite3
from unittest.mock import patch

import pytest
import bank
import db

NOW = 2_000_000_000.125
DAY = 86400


def family():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript(db.SCHEMA)
    bank.add_kid(c, 'Precision')
    c.execute('UPDATE kids SET savings_cents=25,wallet_cents=10000,savings_accrued_ts=?', (NOW,))
    c.commit()
    return c


def assert_money(c, principal, carry, credits):
    kid = c.execute('SELECT * FROM kids').fetchone()
    assert kid['savings_cents'] == principal
    # Read the persisted rational independently, not the implementation parser.
    assert F(kid['savings_interest_exact'].split('@')[0]) == carry
    assert [r[0] for r in c.execute("SELECT amount_cents FROM transactions WHERE kind='interest' ORDER BY id")] == credits


@pytest.mark.parametrize('principal', [1, 3, 25, 125, 333, 10000])
@pytest.mark.parametrize('pct', ['0.1', '0.5', '1', '4', '12.5'])
@pytest.mark.parametrize('days', [1, 7, 14, 30])
def test_twenty_periods_fraction_oracle(principal, pct, days):
    with patch('bank.time.time', return_value=NOW):
        c = family()
        try:
            c.execute('UPDATE kids SET savings_cents=?', (principal,)); c.commit()
            bank.set_rates(c, savings_weekly_pct=float(pct), savings_days=days)
            bank.set_auto_interest(c, True)
            bank.fast_forward(c, days*20)
            bank.all_kids_state(c)
            carry, credits = F(0), []
            for _ in range(20):
                earned = carry + principal*F(pct)/100
                paid = max(0, round(earned))
                principal += paid
                carry = earned-paid
                if paid:
                    credits.append(paid)
            assert_money(c, principal, carry, credits)
        finally:
            c.close()


@pytest.mark.parametrize('direction', [-1, 0, 1])
def test_fractional_clock_on_both_sides_of_claim_midpoint(direction):
    clock = [NOW]
    with patch('bank.time.time', side_effect=lambda: clock[0]):
        c = family()
        try:
            bank.set_rates(c, savings_weekly_pct=2, savings_days=1)
            midpoint = NOW+DAY
            clock[0] = (math.nextafter(midpoint, -math.inf) if direction < 0
                        else math.nextafter(midpoint, math.inf) if direction > 0 else midpoint)
            earned = F(25)*F('0.02')*(F(clock[0])-F(NOW))/DAY
            paid = max(0, round(earned))
            bank.set_auto_interest(c, True)
            kid = c.execute('SELECT * FROM kids').fetchone()
            assert kid['savings_cents'] == 25+paid
            assert F(kid['savings_interest_exact'].split('@')[0]) == earned-paid
        finally:
            c.close()


@pytest.mark.parametrize('pct', [math.nextafter(2., 0.), 2., math.nextafter(2., math.inf)])
def test_real_settings_both_sides_of_half_cent(pct):
    with patch('bank.time.time', return_value=NOW):
        c = family()
        try:
            bank.set_rates(c, savings_weekly_pct=pct, savings_days=1)
            bank.set_auto_interest(c, True)
            principal, carry, credits = 25, F(0), []
            for _ in range(12):
                bank.fast_forward(c, 1)
                bank.all_kids_state(c)
                earned = carry+principal*F(str(pct))/100
                paid = max(0, round(earned))
                principal += paid
                carry = earned-paid
                if paid:
                    credits.append(paid)
                assert_money(c, principal, carry, credits)
        finally:
            c.close()


def test_continuous_fractional_seconds_money_terms_and_rewind():
    clock = [NOW]
    with patch('bank.time.time', side_effect=lambda: clock[0]):
        c = family()
        try:
            bank.set_rates(c, savings_weekly_pct=1, savings_days=1)
            bank.set_auto_interest(c, True)
            principal, carry, anchor = 25, F(0), F(NOW)
            clock[0] = NOW+.125
            carry += principal*F('0.01')*(F(clock[0])-anchor)/DAY
            anchor = F(clock[0])
            assert bank.move_to_savings(c, 1, 100)[0]
            principal += 100
            clock[0] = NOW+43200.375
            carry += principal*F('0.01')*(F(clock[0])-anchor)/DAY
            anchor = F(clock[0])
            new_pct = math.nextafter(1., math.inf)
            bank.set_rates(c, savings_weekly_pct=new_pct, savings_days=2)
            assert_money(c, principal, carry, [])
            credits = []
            for _ in range(4):
                clock[0] += 2*DAY
                earned = carry+principal*F(str(new_pct))/100
                paid = max(0, round(earned))
                carry = earned-paid
                principal += paid
                if paid:
                    credits.append(paid)
                bank.all_kids_state(c)
                assert_money(c, principal, carry, credits)
            bank.set_auto_interest(c, False)
            clock[0] -= .25
            bank.set_rates(c, savings_weekly_pct=3)
            assert_money(c, principal, carry, credits)
            assert bank.collect_rewards(c, 1, 'interest') == max(0, round(carry))
        finally:
            c.close()


@pytest.mark.parametrize('carry', [math.nextafter(.5, 0.), .5, math.nextafter(.5, math.inf), -.5, -0.1])
def test_legacy_real_carry_migration_preserves_every_bit(carry):
    with patch('bank.time.time', return_value=NOW):
        c = family()
        try:
            c.execute('ALTER TABLE kids DROP COLUMN savings_interest_exact')
            c.execute('UPDATE kids SET savings_interest_carry=?', (carry,)); c.commit()
            before = dict(c.execute('SELECT * FROM kids').fetchone())
            assert db._migrate(c)
            after = dict(c.execute('SELECT * FROM kids').fetchone())
            assert after.pop('savings_interest_exact') is None
            assert after == before
            c.commit()
            # Zero elapsed: migrate the already-earned amount, never reprice it.
            bank.set_auto_interest(c, True)
            paid = max(0, round(F(carry)))
            assert_money(c, 25+paid, F(carry)-paid, [paid] if paid else [])
        finally:
            c.close()
