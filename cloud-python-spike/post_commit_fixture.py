"""Injected into the disposable test worker only; never imported by worker.py."""


def fail_after_collect_commit(operation, conn, snapshot):
    original_commit = conn.commit
    commits = 0

    def observed_commit():
        nonlocal commits
        original_commit()
        commits += 1

    def run():
        conn.commit = observed_commit
        result = operation()  # Calls the unmodified bank.collect_rewards.
        if commits != 1 or result != 701:
            raise AssertionError(f'expected one completed inner commit and collection 701, got {commits}, {result}')
        inside = snapshot(conn)
        if (inside['bonds'][0]['collected'] != 1 or
                [row['kind'] for row in inside['ledger'][-3:]] != ['allowance', 'interest', 'bond_payout'] or
                inside['history'][-1]['wallet_cents'] != 1225):
            raise AssertionError(f'collection writes not visible after inner commit: {inside}')
        raise RuntimeError('injected after inner commit returned')

    return run
