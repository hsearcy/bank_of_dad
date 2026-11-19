"""Shared first-visit sample: ordinary domain actions, never a copied database."""
import bank


def create_sample(conn):
    bank.add_kid(conn, 'Sample Kid')
    bank.gift(conn, 1, 200, 'Sample money — try your bank')
    bank.move_to_savings(conn, 1, 100)
    bank.buy_bond(conn, 1, '1mo', 100)
    rate = bank.savings_weekly_rate(conn)
    age = min(7 * 86400 / (100 * rate), 6 * 86400) if rate > 0 else 0
    conn.execute('UPDATE kids SET created_ts=created_ts-?, savings_accrued_ts=savings_accrued_ts-? WHERE id=1', (age, age))
    conn.execute('UPDATE transactions SET ts=ts-? WHERE kid_id=1', (age,))
    conn.execute('UPDATE history SET ts=ts-? WHERE kid_id=1', (age,))
    conn.execute('UPDATE bonds SET buy_ts=buy_ts-?, mature_ts=mature_ts-? WHERE kid_id=1', (age, age))
    conn.commit()
