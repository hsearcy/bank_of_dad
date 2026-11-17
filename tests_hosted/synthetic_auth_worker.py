"""Unauthenticated loopback test fixture only. Never deploy."""
import json
from urllib.parse import urlparse, parse_qs
from workers import WorkerEntrypoint, DurableObject, Response
from hosted.family_auth import FamilyAuthorization

class SyntheticAuth(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.storage = ctx.storage

    async def fetch(self, request):
        try:
            q = parse_qs(urlparse(request.url).query)
            action = q['action'][0]
            args = json.loads(q.get('args', ['[]'])[0])
            now = int(q.get('now', ['1000'])[0])
            auth = FamilyAuthorization(self.storage, clock=lambda: now)
            if action == 'snapshot':
                tables = ('kids', 'bonds', 'transactions', 'history', 'settings',
                          'hosted_schema', 'sqlite_sequence', 'hosted_operations', 'local_operations')
                result = auth.bank._transaction(lambda c: {
                    table: c.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall()
                    for table in tables})
            elif action == 'fault':
                original = auth.bank.conn.execute
                def fail(sql, params=()):
                    result = original(sql, params)
                    if sql.startswith('INSERT INTO local_operations'):
                        raise RuntimeError('after bank write and both operation records')
                    return result
                auth.bank.conn.execute = fail
                result = auth.mutate(*args)
            else:
                result = getattr(auth, action)(*args)
            return Response(json.dumps({'result': result}), headers={'content-type':'application/json'})
        except Exception as e:
            return Response(json.dumps({'error': str(e), 'type': type(e).__name__}), status=500, headers={'content-type':'application/json'})

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        q = parse_qs(urlparse(request.url).query)
        name = q.get('object', ['one'])[0]
        return await self.env.AUTH.get(self.env.AUTH.idFromName(name)).fetch(request.url)
