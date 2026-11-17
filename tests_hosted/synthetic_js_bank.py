"""TEST ONLY: synthetic bootstrap and fault injection. Never deploy this fixture."""
from workers import WorkerEntrypoint
from hosted.object import FamilyBank, _rpc_result
import json
import bank

FAMILY = 'a' * 32
OWNER = {'issuer': 'https://issuer.test', 'subject': 'synthetic-owner'}
NOW = 2_000_000_000
# Fixture-only deterministic domain time, not a production clock change.
bank.now_ts = lambda conn: NOW

class SyntheticJSBank(FamilyBank):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self._authorization.clock = lambda: 1001

    async def fixture_setup(self, family=FAMILY):
        auth = self._authorization
        auth.clock = lambda: 1000
        auth.bootstrap_privileged(family, OWNER)
        active = auth.issue_grant_privileged(family, OWNER, 100)
        expired = auth.issue_grant_privileged(family, OWNER, 1)
        revoked = auth.issue_grant_privileged(family, OWNER, 100)
        auth.revoke(family, revoked)
        auth.clock = lambda: 1001
        return json.dumps({'active': active, 'expired': expired, 'revoked': revoked})

    async def fixture_echo(self, value):
        # JS-origin structured values cross the actual RPC boundary into Python.
        # The output uses the unmodified production result serializer.
        return _rpc_result(value)

    async def fixture_snapshot(self):
        def snapshot(c):
            schema = c.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name").fetchall()
            result = {'schema': schema, 'tables': {}}
            for item in schema:
                if item['type'] == 'table':
                    name = item['name']
                    quoted = '"' + name.replace('"', '""') + '"'
                    # Sort by serialized row rather than assuming every table has a rowid.
                    rows = c.execute('SELECT * FROM ' + quoted).fetchall()
                    result['tables'][name] = sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))
            return result
        return _rpc_result(self._authorization.bank._transaction(snapshot))

    async def fixture_probe_snapshot(self, target, delta):
        # Disposable synthetic state only: demonstrate that snapshots see metadata
        # changes which ordinary application rows would miss.
        statements = {
            'marker': 'UPDATE hosted_schema SET version=version+?',
            'sequence': "UPDATE sqlite_sequence SET seq=seq+? WHERE name='kids'",
        }
        self._authorization.bank._transaction(lambda c: c.execute(statements[target], (delta,)).fetchall())

    async def fixture_schema_collision(self, action):
        statements = {
            'table': 'CREATE TABLE "_schema" (value TEXT)',
            'index': 'CREATE INDEX fixture_schema_index ON "_schema" (value)',
            'cleanup': 'DROP TABLE IF EXISTS "_schema"',
        }
        self._authorization.bank._transaction(lambda c: c.execute(statements[action]).fetchall())

    async def fixture_seed_allowance(self):
        self._authorization.bank._transaction(lambda c: c.execute(
            'UPDATE kids SET allowance_anchor_ts=? WHERE id=1',
            (NOW - 14 * 86400 - 60,)).fetchall())

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
