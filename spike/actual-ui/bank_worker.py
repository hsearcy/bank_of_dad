"""SYNTHETIC LOOPBACK FIXTURE ONLY. Never deploy or use real identities/data."""
from workers import WorkerEntrypoint, DurableObject, Response
from js import WebSocketPair
from pyodide.ffi import create_proxy
from hosted.object import _rpc_result
from hosted.family_auth import FamilyAuthorization
from revision import RevisionFixture
import bank
import json
from dashboard_contract import cfg

OWNER = {'issuer': 'https://synthetic.invalid', 'subject': 'owner'}

class SyntheticLiveBank(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self._authorization = FamilyAuthorization(ctx.storage)
        self._revision = RevisionFixture(self._authorization)
        self.sessions = {}
        self.serial = 0
        self.clock_offset = 0

    async def fixture_setup(self, family):
        a = self._authorization
        self._revision.bootstrap_fresh(family, OWNER)
        one = a.issue_grant_privileged(family, OWNER, 3600)
        two = a.issue_grant_privileged(family, OWNER, 3600)
        self._revision.mutate(family, one, 'fixture-kid', 'add_kid', ['Synthetic child', '🦊', '#123456'])
        return _rpc_result([one, two])

    async def fixture_snapshot(self):
        # Test-driver only: capture every table (including sqlite_sequence) and
        # the entire catalog in distinct namespaces, with deterministic rows.
        def snapshot(c):
            schema = c.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name").fetchall()
            result = {"schema": schema, "tables": {}}
            for item in schema:
                if item["type"] == "table":
                    name = item["name"]
                    quoted = '"' + name.replace('"', '""') + '"'
                    rows = c.execute("SELECT * FROM " + quoted).fetchall()
                    result["tables"][name] = sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))
            return result
        return _rpc_result(self._authorization.bank._transaction(snapshot))

    async def fixture_dashboard(self, family, token):
        a = self._authorization
        def read(c):
            a._auth(c, family, token)
            revision = self._revision._revision(c)
            return {'kids': a.bank._read(c, 'kids', []), 'config': cfg(c),
                    'server_now': bank.now_ts(c), 'revision': revision}
        original = bank.now_ts
        try:
            bank.now_ts = lambda c: original(c) + self.clock_offset
            return _rpc_result(a.bank._transaction(read))
        finally:
            bank.now_ts = original

    async def fixture_clock(self, family, token, seconds):
        self._revision.snapshot(family, token)
        self.clock_offset = int(seconds)
        return _rpc_result(True)

    async def fixture_short_grant(self, family, token):
        self._revision.snapshot(family, token)
        return _rpc_result(self._authorization.issue_grant_privileged(family, OWNER, 4))

    async def fixture_revoke(self, family, token):
        self._authorization.revoke(family, token)
        return _rpc_result(True)

    async def fixture_transactions(self, family, token, kid_id):
        a = self._authorization
        def read(c):
            a._auth(c, family, token)
            revision = self._revision._revision(c)
            return {'transactions': a.bank._read(c, 'transactions', [kid_id]),
                    'server_now': bank.now_ts(c), 'revision': revision}
        return _rpc_result(a.bank._transaction(read))

    async def fetch(self, request):
        family = request.headers.get('x-fixture-family')
        token = request.headers.get('x-fixture-token')
        try:
            self._revision.snapshot(family, token)
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

    def _notify(self, changed_family, revision):
        for sid, (ws, family, token, proxy) in list(self.sessions.items()):
            if family != changed_family:
                continue
            try:
                self._revision.snapshot(family, token)
            except Exception:
                try: ws.close(1008, 'denied')
                except Exception: pass
                continue
            try: ws.send(json.dumps({'type': 'invalidate', 'revision': revision}))
            except Exception: pass

    async def fixture_mutate(self, family, token, operation_id, action, args, drop_notification=False):
        if action not in ('add_kid', 'gift', 'save', 'withdraw', 'spend', 'invest', 'collect_rewards'):
            raise ValueError('unsupported synthetic mutation')
        result, revision, changed = self._revision.mutate(family, token, operation_id, action, args)
        if changed and not drop_notification:
            self._notify(family, revision)
        return _rpc_result(result)

    async def read(self, family, token, action, args):
        if action != 'kids' or args != []:
            raise ValueError('unsupported synthetic read')
        return _rpc_result(self._revision.snapshot(family, token)['kids'])

class Default(WorkerEntrypoint):
    pass
