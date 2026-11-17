"""SYNTHETIC LOOPBACK FIXTURE ONLY. Never deploy or use real identities/data."""
from workers import WorkerEntrypoint, Response
from js import WebSocketPair
from pyodide.ffi import create_proxy
from hosted.object import FamilyBank, _rpc_result
import bank
from dashboard_contract import cfg

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

    async def fixture_dashboard(self, family, token):
        a = self._authorization
        def read(c):
            a._auth(c, family, token)
            return {'kids': a.bank._read(c, 'kids', []), 'config': cfg(c),
                    'server_now': bank.now_ts(c)}
        return _rpc_result(a.bank._transaction(read))

    async def fixture_transactions(self, family, token, kid_id):
        a = self._authorization
        def read(c):
            a._auth(c, family, token)
            return {'transactions': a.bank._read(c, 'transactions', [kid_id]),
                    'server_now': bank.now_ts(c)}
        return _rpc_result(a.bank._transaction(read))

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

    async def fixture_mutate(self, family, token, operation_id, action, args):
        # Only the minimal actual UI contract is exposed in this fixture.
        if action not in ('gift', 'save', 'spend'):
            raise ValueError('unsupported synthetic mutation')
        a = self._authorization
        def exists(c):
            a._auth(c, family, token)
            return bool(c.execute('SELECT operation_id FROM local_operations WHERE operation_id=?', (operation_id,)).fetchone())
        # No await between this check, the real atomic bank mutation, and push.
        prior = a.bank._transaction(exists)
        result = a.mutate(family, token, operation_id, action, args)
        success = result[0] is True if isinstance(result, list) else bool(result)
        if not prior and success:
            self.notify(True)
        return _rpc_result(result)

    async def mutate(self, family, token, operation_id, action, args):
        return await self.fixture_mutate(family, token, operation_id, action, args)

class Default(WorkerEntrypoint):
    pass
