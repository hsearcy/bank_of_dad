"""Unauthenticated synthetic loopback test harness, NEVER DEPLOY."""
import json
from workers import WorkerEntrypoint, Response
from hosted.directory_object import FamilyDirectory

class InjectedFailure(RuntimeError):
    pass

class FaultSQL:
    def __init__(self, original, prefix):
        self.original, self.prefix = original, prefix
    def exec(self, statement, *args):
        result = self.original.exec(statement, *args)
        if statement.startswith(self.prefix):
            raise InjectedFailure('after actual insert')
        return result

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

            if action == 'snapshot':
                def snapshot(c):
                    return {table: sorted(c.execute('SELECT * FROM ' + table).fetchall(), key=lambda row: json.dumps(row, sort_keys=True))
                            for table in ('directory_schema', 'families', 'memberships', 'sessions', 'invitations')}
                return Response(json.dumps({'result':self.directory._run(snapshot)}), headers={'content-type':'application/json'})
            if action == 'drop_invitations':
                self.directory._transaction(lambda c: c.execute('DROP TABLE invitations'))
                return Response(json.dumps({'result':True}), headers={'content-type':'application/json'})
            if action in ('fail_membership', 'fail_session'):
                prefix = 'INSERT INTO memberships' if action == 'fail_membership' else 'INSERT INTO sessions'
                original = self.directory.conn._sql
                self.directory.conn._sql = FaultSQL(original, prefix)
                try:
                    self.directory.accept_invite(*args)
                    raise AssertionError('fault did not fire')
                finally:
                    self.directory.conn._sql = original
            if action not in ('create_family', 'issue_session', 'authorize', 'members', 'my_memberships', 'revoke', 'invite', 'accept_invite'):
                raise ValueError('unknown')
            result = getattr(self.directory, action)(*args)
            return Response(json.dumps({'result':result}), headers={'content-type':'application/json'})
        except Exception as e:
            return Response(json.dumps({'error':type(e).__name__}), status=400, headers={'content-type':'application/json'})

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await self.env.DIRECTORY.get(self.env.DIRECTORY.idFromName('directory')).fetch(request)
