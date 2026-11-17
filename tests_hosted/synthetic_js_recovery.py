"""LOCAL TEST ONLY: unauthenticated synthetic RPC fixture. Never deploy."""
from workers import WorkerEntrypoint
from hosted.object import FamilyBank, _rpc_result
from hosted.recovery import RecoveryAuthorization
from hosted.family_auth import FamilyAuthorization

F = 'a'*32
OWNER = {'issuer':'https://issuer.test','subject':'owner'}

class SyntheticRecovery(FamilyBank):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.storage = ctx.storage
        self._authorization = RecoveryAuthorization(ctx.storage, clock=lambda:1000)

    async def fixture_setup(self, legacy=False):
        a = FamilyAuthorization(self.storage, clock=lambda:1000) if legacy else self._authorization
        a.bootstrap_privileged(F, OWNER)
        return a.issue_grant_privileged(F, OWNER, 100)

    async def fixture_invoke(self, method, args):
        if method not in ('read','mutate','pending','outcome','acknowledge'):
            raise ValueError('unknown method')
        return _rpc_result(getattr(self._authorization, method)(*args))

    async def fixture_v1_read(self, token):
        old = self._authorization
        self._authorization = FamilyAuthorization(self.storage, clock=lambda:1000)
        try:
            return await FamilyBank.read(self, F, token, 'kids', [])
        finally:
            self._authorization = old

    async def fixture_fault(self, args, prefix):
        a = self._authorization; original = a.bank.conn.execute
        def fail(sql, params=()):
            value = original(sql, params)
            if sql.startswith(prefix): raise RuntimeError('synthetic injected fault')
            return value
        a.bank.conn.execute = fail
        try: return _rpc_result(a.mutate(*args))
        finally: a.bank.conn.execute = original

class Default(WorkerEntrypoint):
    pass
