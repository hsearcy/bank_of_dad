"""Synthetic loopback harness ONLY. Never deploy or bind publicly."""
import json
from urllib.parse import urlparse, parse_qs
from workers import WorkerEntrypoint, Response
from hosted.object import FamilyBank
import bank

bank.time.time = lambda: 1700000000.0

class SyntheticBank(FamilyBank):
    async def fetch(self, request):
        try:
            q = parse_qs(urlparse(request.url).query)
            action = q['action'][0]
            args = json.loads(q.get('args', ['[]'])[0])
            if action == 'read':
                result = self.service.read(args[0], args[1])
            elif action == 'fail_after_commit':
                def fail(conn):
                    bank.gift(conn, 1, 900, 'rollback')
                    raise RuntimeError('after inner commit')
                result = self.service._transaction(fail)
            else:
                result = self.service.mutate(q['id'][0], action, args)
            return Response(json.dumps({'result': result}), headers={'content-type': 'application/json'})
        except Exception as e:
            return Response(json.dumps({'error': str(e)}), status=500, headers={'content-type': 'application/json'})

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        q = parse_qs(urlparse(request.url).query)
        name = q.get('family', ['a'])[0]
        return await self.env.BANK.get(self.env.BANK.idFromName(name)).fetch(request.url)
