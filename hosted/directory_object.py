"""Directory DO. Internal binding only; no fetch handler or deployment config."""
from workers import DurableObject
from .directory import Directory

class FamilyDirectory(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.directory = Directory(ctx.storage)

    async def create_family(self, verified_principal):
        return self.directory.create_family(verified_principal)

    async def issue_session(self, verified_principal):
        return self.directory.issue_session(verified_principal)

    async def authorize(self, session, family):
        return self.directory.authorize(session, family)

    async def my_memberships(self, session):
        return self.directory.my_memberships(session)

    async def members(self, session, family):
        return self.directory.members(session, family)

    async def revoke(self, session):
        return self.directory.revoke(session)

    async def invite(self, session, family, role, ttl):
        return self.directory.invite(session, family, role, ttl)

    async def accept_invite(self, verified_principal, token):
        return self.directory.accept_invite(verified_principal, token)
