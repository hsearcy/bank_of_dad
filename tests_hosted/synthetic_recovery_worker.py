"""Loopback-only synthetic fixture: unauthenticated and NEVER deploy."""
import json
from urllib.parse import urlparse, parse_qs
from workers import WorkerEntrypoint, DurableObject, Response
from hosted.recovery import RecoveryAuthorization


def full_snapshot(auth):
    def op(c):
        schema = c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        tables = {}
        for row in schema:
            if row['type'] == 'table':
                name = row['name']
                quoted = '"' + name.replace('"', '""') + '"'
                rows = c.execute('SELECT * FROM ' + quoted).fetchall()
                tables[name] = sorted(rows, key=lambda r: json.dumps(r, sort_keys=True))
        return {'schema': schema, 'tables': tables}
    return auth.bank._transaction(op)

class RecoveryDO(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.storage = ctx.storage

    async def invoke(self, encoded):
        try:
            request = json.loads(encoded)
            action = request['action']
            if action not in ('bootstrap_privileged', 'issue_grant_privileged', 'set_member',
                              'revoke', 'read', 'mutate', 'pending', 'outcome', 'acknowledge', 'fault', 'snapshot'):
                raise ValueError('unknown test action')
            auth = RecoveryAuthorization(self.storage, clock=lambda: 1000)
            if action == 'snapshot':
                result = full_snapshot(auth)
            elif action == 'fault':
                original = auth.bank.conn.execute
                def fail(sql, params=()):
                    result = original(sql, params)
                    if sql.startswith('INSERT INTO local_recovery'):
                        raise RuntimeError('injected after recovery record')
                    return result
                auth.bank.conn.execute = fail
                result = auth.mutate(*request['args'])
            else:
                result = getattr(auth, action)(*request['args'])
            return json.dumps({'result': result})
        except Exception as error:
            return json.dumps({'error': str(error), 'type': type(error).__name__})

class RelayDO(DurableObject):
    async def fetch(self, request):
        q = parse_qs(urlparse(request.url).query)
        encoded = q.get('request', ['{}'])[0]
        target = self.env.RECOVERY.get(self.env.RECOVERY.idFromName('family'))
        response = await target.invoke(encoded)
        return Response(response, headers={'content-type': 'application/json'})

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await self.env.RELAY.get(self.env.RELAY.idFromName('relay')).fetch(request.url)
