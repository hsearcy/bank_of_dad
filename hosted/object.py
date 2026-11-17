"""Experimental DO boundary: no HTTP handler or real identity integration.

A trusted caller must select the family DO and provision grants internally.
"""
import json
from workers import DurableObject
from js import JSON
from .family_auth import FamilyAuthorization


def _rpc_result(value):
    """Clone JSON data to JS; in particular, preserve Python None as null.

    Plain Python dicts cross as proxy objects, and Pyodide to_js(None) becomes
    undefined (which JSON drops). The guarded domain returns only JSON values.
    """
    return JSON.parse(json.dumps(value, ensure_ascii=False, allow_nan=False))

class FamilyBank(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self._authorization = FamilyAuthorization(ctx.storage)

    async def read(self, family, token, action, args):
        return _rpc_result(self._authorization.read(family, token, action, args))

    async def mutate(self, family, token, operation_id, action, args):
        return _rpc_result(self._authorization.mutate(family, token, operation_id, action, args))
