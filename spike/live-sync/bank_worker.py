"""SYNTHETIC LOOPBACK FIXTURE ONLY. Never deploy or use real identities/data."""
import json
from workers import WorkerEntrypoint, Response
from js import WebSocketPair
from pyodide.ffi import create_proxy
from hosted.object import FamilyBank, _rpc_result

OWNER = {'issuer': 'https://synthetic.invalid', 'subject': 'owner'}

class SyntheticLiveBank(FamilyBank):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.sessions = {}
        self.serial = 0

    async def fixture_setup(self, family):
        a = self._authorization
        a.bootstrap_privileged(family, OWNER)
        one = a.issue_grant_privileged(family, OWNER, 3600)
        two = a.issue_grant_privileged(family, OWNER, 3600)
        a.mutate(family, one, 'fixture-kid', 'add_kid', ['Synthetic child', '🦊', '#123456'])
        return _rpc_result([one, two])

    async def fetch(self, request):
        family = request.headers.get('x-fixture-family')
        token = request.headers.get('x-fixture-token')
        try:
            self._authorization.read(family, token, 'kids', [])
        except Exception:
            return Response('denied', status=403)
        client, server = WebSocketPair.new().object_values()
        server.accept()
        self.serial += 1
        sid = self.serial
        def closed(event):
            item = self.sessions.pop(sid, None)
            if item:
                item[3].destroy()
        close_proxy = create_proxy(closed)
        server.addEventListener('close', close_proxy)
        self.sessions[sid] = (server, family, token, close_proxy)
        return Response(None, status=101, web_socket=client)

    def notify(self, send):
        for sid, (ws, family, token, proxy) in list(self.sessions.items()):
            try:
                self._authorization.read(family, token, 'kids', [])
            except Exception:
                try: ws.close(1008, 'denied')
                except Exception: pass
                continue
            if send:
                try: ws.send('{"type":"invalidate"}')
                except Exception: pass

    async def fixture_revoke(self, family, token):
        self._authorization.revoke(family, token)
        self.notify(False)
        return True

    async def fixture_expire(self, family, token):
        from hosted.directory import _digest
        a = self._authorization
        def op(c):
            a._auth(c, family, token)
            c.execute('UPDATE local_grants SET expires=0 WHERE digest=?', (_digest(token),))
        a.bank._transaction(op)
        self.notify(False)
        return True

    async def fixture_allowance_due(self, family, token):
        import time
        def op(c):
            self._authorization._auth(c, family, token)
            c.execute('UPDATE kids SET allowance_anchor_ts=? WHERE id=1', (int(time.time()) - 14 * 86400,))
        self._authorization.bank._transaction(op)
        return True

    async def fixture_mature_zero(self, family, token):
        import time
        def op(c):
            self._authorization._auth(c, family, token)
            past = int(time.time()) - 1
            c.execute('UPDATE bonds SET buy_ts=?, mature_ts=?, payout_cents=principal_cents WHERE kid_id=2', (past, past))
        self._authorization.bank._transaction(op)
        return True

    async def fixture_mutate(self, family, token, operation_id, action, args, fault=False):
        a = self._authorization
        # No await between checking idempotency, committing, and notifying: the
        # DO serially executes this synchronous sequence. Result is still checked.
        def bank_rows(c):
            return [c.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall()
                    for table in ('kids', 'bonds', 'transactions', 'history')]
        def exists(c):
            a._auth(c, family, token)
            prior = bool(c.execute('SELECT operation_id FROM local_operations WHERE operation_id=?', (operation_id,)).fetchone())
            return prior, bank_rows(c) if action == 'collect_rewards' and not prior else None
        prior, before = a.bank._transaction(exists)
        original = a.bank.conn.execute
        if fault:
            def fail(sql, params=()):
                result = original(sql, params)
                if sql.startswith('INSERT INTO local_operations'):
                    raise RuntimeError('fixture injected failure')
                return result
            a.bank.conn.execute = fail
        try:
            result = a.mutate(family, token, operation_id, action, args)
        finally:
            a.bank.conn.execute = original
        # A zero-gain matured bond returns 0 but transfers principal and writes
        # history. Compare bank rows for collection, not its numeric result.
        success = result[0] is True if isinstance(result, list) else bool(result)
        if not prior and action == 'collect_rewards' and not success:
            success = a.bank._transaction(bank_rows) != before
        if not prior and success:
            self.notify(True)
        return _rpc_result(result)

    async def mutate(self, family, token, operation_id, action, args):
        return await self.fixture_mutate(family, token, operation_id, action, args)

class Default(WorkerEntrypoint):
    pass
