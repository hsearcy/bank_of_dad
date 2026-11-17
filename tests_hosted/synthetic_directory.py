"""Unauthenticated synthetic loopback test harness, NEVER DEPLOY."""
import json
from workers import WorkerEntrypoint, Response
from hosted.directory_object import FamilyDirectory

class SyntheticDirectory(FamilyDirectory):
    async def fetch(self, request):
        try:
            data = json.loads(await request.text())
            action, args = data['action'], data.get('args', [])
            if action == 'expire':
                self.directory.clock = lambda: 4000000000
                action = 'accept_invite'
            else:
                self.directory.clock = lambda: 1700000000
            if action == 'rollback':
                def fail(c):
                    c.execute("UPDATE invitations SET used=1")
                    raise RuntimeError('injected rollback')
                self.directory._run(fail)
            if action not in ('create_family', 'issue_session', 'authorize', 'members', 'my_memberships', 'revoke', 'invite', 'accept_invite'):
                raise ValueError('unknown')
            result = getattr(self.directory, action)(*args)
            return Response(json.dumps({'result':result}), headers={'content-type':'application/json'})
        except Exception as e:
            return Response(json.dumps({'error':type(e).__name__}), status=400, headers={'content-type':'application/json'})

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await self.env.DIRECTORY.get(self.env.DIRECTORY.idFromName('directory')).fetch(request)
