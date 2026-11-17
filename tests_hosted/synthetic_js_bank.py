"""TEST ONLY: synthetic bootstrap and fault injection. Never deploy this fixture."""
from workers import WorkerEntrypoint
from hosted.object import FamilyBank, _rpc_result

FAMILY = 'a' * 32
OWNER = {'issuer': 'https://issuer.test', 'subject': 'synthetic-owner'}

class SyntheticJSBank(FamilyBank):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self._authorization.clock = lambda: 1001

    async def fixture_setup(self):
        auth = self._authorization
        auth.clock = lambda: 1000
        auth.bootstrap_privileged(FAMILY, OWNER)
        active = auth.issue_grant_privileged(FAMILY, OWNER, 100)
        expired = auth.issue_grant_privileged(FAMILY, OWNER, 1)
        revoked = auth.issue_grant_privileged(FAMILY, OWNER, 100)
        auth.revoke(FAMILY, revoked)
        auth.clock = lambda: 1001
        import json
        return json.dumps({'active': active, 'expired': expired, 'revoked': revoked})

    async def fixture_snapshot(self):
        tables = ('kids', 'bonds', 'transactions', 'history', 'settings', 'hosted_operations', 'local_operations')
        return _rpc_result(self._authorization.bank._transaction(lambda c: {
            t: c.execute('SELECT * FROM ' + t + ' ORDER BY rowid').fetchall() for t in tables}))

    async def fixture_seed_allowance(self):
        import time
        self._authorization.bank._transaction(lambda c: c.execute(
            'UPDATE kids SET allowance_anchor_ts=? WHERE id=1',
            (int(time.time()) - 14 * 86400 - 60,)).fetchall())

    async def fixture_disable_allowance(self):
        return self._authorization.bank._transaction(lambda c: c.execute(
            'UPDATE kids SET allowance_enabled=0 WHERE id=1').fetchall())

    async def fixture_fault(self, family, token, operation_id, action, args):
        auth = self._authorization
        original = auth.bank.conn.execute
        def fail(sql, params=()):
            result = original(sql, params)
            if sql.startswith('INSERT INTO local_operations'):
                raise RuntimeError('synthetic injected fault')
            return result
        auth.bank.conn.execute = fail
        try:
            return auth.mutate(family, token, operation_id, action, args)
        finally:
            auth.bank.conn.execute = original

class Default(WorkerEntrypoint):
    pass
