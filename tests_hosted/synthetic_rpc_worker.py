"""Synthetic loopback-only cross-DO RPC fixture; never deploy."""
import json
from urllib.parse import parse_qs, urlparse
from workers import WorkerEntrypoint, DurableObject, Response
from hosted.object import FamilyBank

FAMILY = 'a' * 32
OWNER = {'issuer': 'https://issuer.test', 'subject': 'owner'}

class SyntheticRPC(FamilyBank):
    async def fixture_setup(self):
        self._authorization.bootstrap_privileged(FAMILY, OWNER)
        return self._authorization.issue_grant_privileged(FAMILY, OWNER, 100)

class SyntheticCaller(DurableObject):
    async def fetch(self, request):
        q = parse_qs(urlparse(request.url).query)
        stub = self.env.BANK.get(self.env.BANK.idFromName('synthetic'))
        try:
            action = q['action'][0]
            if action == 'setup':
                result = await stub.fixture_setup()
            elif action == 'read':
                result = await stub.read(FAMILY, q['token'][0], 'kids', [])
            elif action == 'mutate':
                result = await stub.mutate(FAMILY, q['token'][0], 'kid', 'add_kid', ['RPC Kid'])
            elif action == 'wrong_family':
                result = await stub.read('b' * 32, q['token'][0], 'kids', [])
            elif action == 'raw':
                result = await stub.mutate('kid2', 'add_kid', ['Bypass'])
            elif action == 'privileged':
                result = await stub.issue_grant_privileged(FAMILY, OWNER, 100)
            elif action == 'private':
                result = await stub._authorization.read(FAMILY, q.get('token', [''])[0], 'kids', [])
            else:
                raise ValueError('unknown')
            if hasattr(result, 'to_py'): result = result.to_py()
            return Response(json.dumps({'result': result}), headers={'content-type': 'application/json'})
        except Exception as e:
            return Response(json.dumps({'error': str(e), 'type': type(e).__name__}), status=500, headers={'content-type': 'application/json'})

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        caller = self.env.CALLER.get(self.env.CALLER.idFromName('synthetic-caller'))
        return await caller.fetch(request.url)
