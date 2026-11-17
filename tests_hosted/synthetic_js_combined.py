"""LOCAL SYNTHETIC TEST FIXTURE ONLY. Never deploy or use real data.

The fixture_* RPCs are deliberately privileged test controls, not an ingress.
The protected surface composes the combined coordinator and exposes no v1 RPC.
"""
import json
from workers import WorkerEntrypoint, DurableObject
from js import JSON
from hosted.object import _rpc_result
from hosted.recovery_revision import RecoveryRevisionAuthorization
from hosted.family_auth import FamilyAuthorization
from hosted.recovery import RecoveryAuthorization

OWNER = {'issuer': 'https://issuer.test', 'subject': 'owner'}
PARENT = {'issuer': 'https://issuer.test', 'subject': 'parent'}

class SyntheticCombined(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self._storage = ctx.storage
        self._authorization = RecoveryRevisionAuthorization(ctx.storage, clock=lambda: 1000)
        self._pause = 'idle'

    async def fixture_gate(self, state=None):
        if state is not None:
            self._pause = state
        return _rpc_result(self._pause)

    async def fixture_grant_owner(self, family):
        # Pretend a new login has verified exactly the same fake principal.
        return _rpc_result(self._authorization.issue_grant_privileged(family, OWNER, 100))

    async def fixture_unrelated(self):
        raise ValueError('unacknowledged operation unrelated failure')

    async def fixture_call(self, method, args):
        # Test-only exact classification at the Python origin, never traceback parsing.
        methods = {'read': self.read, 'snapshot': self.snapshot, 'mutate': self.mutate,
                   'pending': self.pending, 'outcome': self.outcome,
                   'acknowledge': self.acknowledge, 'setup': self.fixture_setup,
                   'full': self.fixture_full_snapshot, 'fault': self.fixture_fault,
                   'sql': self.fixture_sql, 'revoke': self.fixture_revoke,
                   'expire': self.fixture_expire, 'grant_owner': self.fixture_grant_owner,
                   'gate': self.fixture_gate, 'unrelated': self.fixture_unrelated}
        try:
            value = await methods[method](*args)
            return _rpc_result({'ok': True, 'value': json.loads(JSON.stringify(value))})
        except Exception as e:
            categories = {
                (PermissionError, 'denied'): 'denied',
                (ValueError, 'unacknowledged operation'): 'pending',
                (ValueError, 'operation ID reused with different payload'): 'payload',
                (ValueError, 'result mismatch'): 'result',
                (ValueError, 'unknown operation'): 'unknown',
                (RuntimeError, 'synthetic injected fault'): 'fault',
                (RuntimeError, 'unsupported combined schema; no migration available'): 'schema',
            }
            return _rpc_result({'ok': False, 'error': categories.get((type(e), str(e)), 'unexpected'),
                                'detail': type(e).__name__ + ': ' + str(e)})

    async def read(self, family, token, action, args):
        return _rpc_result(self._authorization.read(family, token, action, args))

    async def snapshot(self, family, token):
        return _rpc_result(self._authorization.snapshot(family, token))

    async def mutate(self, family, token, operation, action, args):
        return _rpc_result(self._authorization.mutate(family, token, operation, action, args))

    async def pending(self, family, token):
        return _rpc_result(self._authorization.pending(family, token))

    async def outcome(self, family, token, operation):
        return _rpc_result(self._authorization.outcome(family, token, operation))

    async def acknowledge(self, family, token, operation, result):
        return _rpc_result(self._authorization.acknowledge(family, token, operation, result))

    async def fixture_setup(self, family, version=3):
        cls = {1: FamilyAuthorization, 2: RecoveryAuthorization, 3: RecoveryRevisionAuthorization}[version]
        a = cls(self._storage, clock=lambda: 1000)
        a.bootstrap_privileged(family, OWNER)
        one = a.issue_grant_privileged(family, OWNER, 100)
        two = a.issue_grant_privileged(family, OWNER, 100)
        a.set_member(family, one, PARENT, 'parent', 'active')
        parent = a.issue_grant_privileged(family, PARENT, 100)
        return _rpc_result([one, two, parent])

    async def fixture_revoke(self, family, token):
        self._authorization.revoke(family, token)
        return _rpc_result(True)

    async def fixture_expire(self, family, token):
        self._authorization.clock = lambda: 1100
        try:
            return _rpc_result(self._authorization.pending(family, token))
        finally:
            self._authorization.clock = lambda: 1000

    async def fixture_sql(self, sql):
        # Trusted synthetic corruption/seed only; never expose on a real worker.
        return _rpc_result(self._authorization.bank._transaction(lambda c: c.execute(sql).fetchall()))

    async def fixture_full_snapshot(self):
        def op(c):
            schema = c.execute('SELECT type,name,tbl_name,rootpage,sql FROM sqlite_master ORDER BY type,name').fetchall()
            catalog = c.execute('SELECT type,name,tbl_name,rootpage,sql, typeof(type) AS t0, typeof(name) AS t1, typeof(tbl_name) AS t2, typeof(rootpage) AS t3, typeof(sql) AS t4 FROM sqlite_master ORDER BY type,name').fetchall()
            catalog = [{name: {'type': row['t' + str(i)], 'value': row[name]}
                        for i, name in enumerate(('type', 'name', 'tbl_name', 'rootpage', 'sql'))}
                       for row in catalog]
            tables = {}
            for row in schema:
                if row['type'] == 'table':
                    name = row['name']
                    quoted = '"' + name.replace('"', '""') + '"'
                    columns = c.execute('PRAGMA table_xinfo(' + quoted + ')').fetchall()
                    names = [col['name'] for col in columns if col['hidden'] != 1]
                    expressions = []
                    for i, col in enumerate(names):
                        q = '"' + col.replace('"', '""') + '"'
                        expressions.extend(['CASE WHEN typeof(' + q + ")='blob' THEN hex(" + q + ') ELSE ' + q + ' END AS "v' + str(i) + '"',
                                            'typeof(' + q + ') AS "t' + str(i) + '"'])
                    rows = c.execute('SELECT ' + ','.join(expressions) + ' FROM ' + quoted).fetchall()
                    tagged = [{name: {'type': row['t' + str(i)], 'value': row['v' + str(i)]}
                               for i, name in enumerate(names)} for row in rows]
                    tables[name] = sorted(tagged, key=lambda r: json.dumps(r, sort_keys=True))
            return {'schema': catalog, 'tables': tables}
        return _rpc_result(self._authorization.bank._transaction(op))

    async def fixture_fault(self, args, prefix, phase, operation='mutate'):
        a = self._authorization
        original = a.bank.conn.execute
        def fail(sql, params=()):
            if sql.startswith(prefix) and phase == 'before':
                raise RuntimeError('synthetic injected fault')
            result = original(sql, params)
            if sql.startswith(prefix) and phase == 'after':
                raise RuntimeError('synthetic injected fault')
            return result
        a.bank.conn.execute = fail
        try:
            if operation == 'mutate':
                return _rpc_result(a.mutate(*args))
            if operation == 'acknowledge':
                return _rpc_result(a.acknowledge(*args))
            raise ValueError('unsupported fault target')
        finally:
            a.bank.conn.execute = original

class Default(WorkerEntrypoint):
    pass
