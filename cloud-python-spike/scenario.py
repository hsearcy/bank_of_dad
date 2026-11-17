"""Synthetic inputs only; every operation delegates to the original bank module."""
import bank


def perform(conn, action):
    if action == 'save':
        return bank.move_to_savings(conn, 1, 400)
    if action == 'withdraw':
        return bank.withdraw_savings(conn, 1, 100)
    if action == 'spend':
        return bank.spend(conn, 1, 125, '  toy  ')
    if action == 'invest':
        return bank.buy_bond(conn, 1, '1mo', 200)
    if action == 'advance':
        return bank.fast_forward(conn, 30)
    if action == 'pending':
        kid = conn.execute('SELECT * FROM kids WHERE id=?', (1,)).fetchone()
        state = bank.kid_state(conn, kid, bank.now_ts(conn))
        return {k: state[k] for k in ('pending_allowance_cents', 'pending_interest_cents', 'pending_bonds_cents', 'owned_cents')}
    if action in ('collect', 'fail_collect'):
        return bank.collect_rewards(conn, 1, 'all')
    if action == 'collect_invest':
        return bank.collect_rewards(conn, 1, 'invest')
    raise ValueError('unknown scenario action')
