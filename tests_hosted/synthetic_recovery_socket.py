"""UNSAFE LOCAL SYNTHETIC TEST FIXTURE. Never deploy or use real data."""
import json
from workers import WorkerEntrypoint, DurableObject, Response
from js import WebSocketPair, JSON
from pyodide.ffi import create_proxy
from hosted.object import _rpc_result
from hosted.recovery import _json
from hosted.recovery_revision import RecoveryRevisionAuthorization
from hosted.directory import _digest

OWNER = {'issuer': 'https://issuer.test', 'subject': 'owner'}
PARENT = {'issuer': 'https://issuer.test', 'subject': 'parent'}

class SyntheticRecoverySocket(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.auth = RecoveryRevisionAuthorization(ctx.storage, clock=lambda: 1000)
        self.sockets = {}
        self.serial = 0
        self.gate = 'idle'
        self.send_failures_remaining = 0
        self.send_failures_observed = 0

    async def fixture_gate(self, state=None):
        if state is not None: self.gate = state
        return _rpc_result(self.gate)

    async def fetch(self, request):
        family, token = request.headers.get('x-family'), request.headers.get('x-token')
        try:
            self.auth.snapshot(family, token)
        except PermissionError:
            return Response('denied', status=403)
        client, server = WebSocketPair.new().object_values()
        server.accept()
        self.serial += 1
        sid = self.serial
        def closed(event):
            item = self.sockets.pop(sid, None)
            if item:
                item[3].destroy()
        proxy = create_proxy(closed)
        server.addEventListener('close', proxy)
        self.sockets[sid] = (server, family, token, proxy)
        return Response(None, status=101, web_socket=client)

    def _notify(self, changed_family):
        for ws, family, token, proxy in list(self.sockets.values()):
            if family != changed_family:
                continue
            try:
                # Recheck the authoritative current same-family grant immediately
                # before sending. No await permits another event to interleave.
                self.auth.snapshot(family, token)
            except Exception:
                try: ws.close(1008, 'denied')
                except Exception: pass
                continue
            try:
                if self.send_failures_remaining:
                    self.send_failures_remaining -= 1
                    self.send_failures_observed += 1
                    raise RuntimeError('synthetic socket.send failure')
                ws.send('{"type":"invalidate"}')
            except Exception: pass

    async def snapshot(self, family, token):
        return _rpc_result(self.auth.snapshot(family, token))

    async def pending(self, family, token):
        return _rpc_result(self.auth.pending(family, token))

    async def outcome(self, family, token, operation):
        return _rpc_result(self.auth.outcome(family, token, operation))

    async def mutate(self, family, token, operation, action, args):
        a = self.auth
        def op(c):
            return a._mutate_in_transaction(c, family, token, operation, action, args)
        result, replay = a.bank._transaction(op)
        if not replay:
            self._notify(family)
        return _rpc_result(result)

    async def acknowledge(self, family, token, operation, result):
        a = self.auth
        def op(c):
            issuer, subject = a._auth(c, family, token)
            a._valid_id(operation)
            outcome = a._outcome(c, issuer, subject, operation)
            if outcome is None: raise ValueError('unknown operation')
            if _json(outcome['result']) != _json(result): raise ValueError('result mismatch')
            if not outcome['acknowledged']:
                c.execute('UPDATE local_recovery SET acknowledged=1 WHERE operation_id=? AND issuer=? AND subject=?', (operation, issuer, subject))
                return True
            return False
        changed = a.bank._transaction(op)
        if changed: self._notify(family)
        return _rpc_result(True)

    async def fixture_setup(self, family):
        a = self.auth
        a.bootstrap_privileged(family, OWNER)
        one = a.issue_grant_privileged(family, OWNER, 100)
        two = a.issue_grant_privileged(family, OWNER, 100)
        a.set_member(family, one, PARENT, 'parent', 'active')
        parent = a.issue_grant_privileged(family, PARENT, 100)
        return _rpc_result([one, two, parent])

    async def fixture_grant_owner(self, family):
        return _rpc_result(self.auth.issue_grant_privileged(family, OWNER, 100))

    async def fixture_revoke(self, family, token):
        self.auth.revoke(family, token)
        return _rpc_result(True)

    async def fixture_expire(self, family, token):
        a = self.auth
        def op(c):
            a._auth(c, family, token)
            c.execute('UPDATE local_grants SET expires=0 WHERE digest=?', (_digest(token),))
        a.bank._transaction(op)
        return _rpc_result(True)

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
        return _rpc_result(self.auth.bank._transaction(op))

    async def fixture_send_fault(self, count=0):
        self.send_failures_remaining = count
        return _rpc_result(self.send_failures_observed)

    async def fixture_fault(self, method, args, prefix):
        a = self.auth
        original = a.bank.conn.execute
        def fail(sql, params=()):
            value = original(sql, params)
            if sql.startswith(prefix): raise RuntimeError('synthetic injected fault')
            return value
        a.bank.conn.execute = fail
        try:
            if method == 'mutate': return await self.mutate(*args)
            if method == 'acknowledge': return await self.acknowledge(*args)
            raise ValueError('unsupported fault')
        finally:
            a.bank.conn.execute = original

    async def fixture_call(self, method, args):
        methods = {'snapshot': self.snapshot, 'pending': self.pending, 'outcome': self.outcome,
                   'mutate': self.mutate, 'acknowledge': self.acknowledge,
                   'setup': self.fixture_setup, 'grant': self.fixture_grant_owner,
                   'revoke': self.fixture_revoke, 'expire': self.fixture_expire,
                   'fault': self.fixture_fault, 'full_snapshot': self.fixture_full_snapshot,
                   'send_fault': self.fixture_send_fault}
        try:
            value = await methods[method](*args)
            return _rpc_result({'ok': True, 'value': json.loads(JSON.stringify(value))})
        except Exception as e:
            return _rpc_result({'ok': False, 'error': type(e).__name__ + ': ' + str(e)})

class Default(WorkerEntrypoint):
    pass
