"""Experimental DO boundary: no HTTP handler or real identity integration.

A trusted caller must select the family DO and provision grants internally.
"""
from workers import DurableObject
from .family_auth import FamilyAuthorization

class FamilyBank(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self._authorization = FamilyAuthorization(ctx.storage)

    async def read(self, family, token, action, args):
        return self._authorization.read(family, token, action, args)

    async def mutate(self, family, token, operation_id, action, args):
        return self._authorization.mutate(family, token, operation_id, action, args)
