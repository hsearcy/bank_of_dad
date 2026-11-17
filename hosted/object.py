"""DO boundary: one instance and SQLite storage per family.

This class deliberately defines no HTTP handler. A future trusted Worker must
resolve an authenticated, authorized family to its DO binding before calling it.
"""
from workers import DurableObject
from .service import FamilyService


class FamilyBank(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.service = FamilyService(ctx.storage)

    async def mutate(self, operation_id, action, args):
        return self.service.mutate(operation_id, action, args)

    async def read(self, action, args):
        return self.service.read(action, args)
