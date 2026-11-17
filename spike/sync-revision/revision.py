"""Local synthetic revision helper. Not an RPC or a production service.

The caller must use the atomic fresh bootstrap and exclusively route all bank
mutations through this object. It is not a migration for any existing bank.
"""

from hosted.directory import _family, _principal
from hosted.family_auth import _SCHEMA
import hashlib
import json

_TABLE = 'CREATE TABLE fixture_revision (version INTEGER NOT NULL, revision INTEGER NOT NULL)'
_MAX_REVISION = 2**53 - 1
# Exact catalog for this synthetic prototype's supported schema. Schema changes
# require an explicit new fixture version, never implicit adoption/migration.
_CATALOG_SHA256 = 'e59e77fba74180506e6aaeac09ebc4f520b2b4403df26ce6a794138c0ec07e86'


class RevisionFixture:
    def __init__(self, authorization):
        self.auth = authorization

    def bootstrap_fresh(self, family, owner, fault=False):
        """Initialize the entire disposable schema and marker in one transaction."""
        _family(family)
        principal = _principal(owner)
        def install(c):
            if c.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall():
                raise RuntimeError('fresh empty fixture required; no migration available')
            self.auth.bank._initialize(c)
            for sql in _SCHEMA.values():
                c.execute(sql)
            c.execute('INSERT INTO local_auth_schema VALUES (1, ?)', (family,))
            c.execute("INSERT INTO local_members VALUES (?, ?, 'owner', 'active')", principal)
            c.execute(_TABLE)
            c.execute('INSERT INTO fixture_revision VALUES (1, 0)')
            self.auth._check(c, family)
            self._revision(c)
            if fault:
                raise RuntimeError('synthetic bootstrap fault')
        self.auth.bank._transaction(install)

    def _revision(self, c):
        catalog = c.execute('SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name').fetchall()
        values = [[row[k] for k in ('type', 'name', 'tbl_name', 'sql')] for row in catalog]
        digest = hashlib.sha256(json.dumps(values, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        if digest != _CATALOG_SHA256:
            raise RuntimeError('unsupported fixture schema; no migration available')
        rows = c.execute("SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name='fixture_revision' OR tbl_name='fixture_revision'").fetchall()
        if len(rows) != 1 or tuple(rows[0][k] for k in ('type', 'name', 'tbl_name', 'sql')) != ('table', 'fixture_revision', 'fixture_revision', _TABLE):
            raise RuntimeError('unsupported revision schema; no migration available')
        rows = c.execute('SELECT version, revision FROM fixture_revision').fetchall()
        if len(rows) != 1 or type(rows[0]['version']) is not int or rows[0]['version'] != 1 or type(rows[0]['revision']) is not int or not 0 <= rows[0]['revision'] <= _MAX_REVISION:
            raise RuntimeError('invalid revision marker')
        return rows[0]['revision']

    def snapshot(self, family, token):
        def read(c):
            self.auth._auth(c, family, token)
            revision = self._revision(c)
            return {'kids': self.auth.bank._read(c, 'kids', []), 'revision': revision}
        return self.auth.bank._transaction(read)

    def mutate(self, family, token, operation_id, action, args, fault=False):
        if action not in ('add_kid', 'gift', 'save', 'withdraw', 'spend', 'invest', 'collect_rewards'):
            raise ValueError('unsupported fixture mutation')
        def domain_rows(c):
            # Explicit domain tables only: operation ownership and idempotency
            # bookkeeping are not bank changes. Catalog validation fences schema.
            return [c.execute('SELECT * FROM ' + table + ' ORDER BY id').fetchall()
                    for table in ('kids', 'bonds', 'transactions', 'history')]
        def operation(c):
            self.auth._auth(c, family, token)
            revision = self._revision(c)
            before = domain_rows(c)
            self.auth.bank._combined_mutation = True
            try:
                result, replay = self.auth._mutate_in_transaction(c, family, token, operation_id, action, args)
            finally:
                self.auth.bank._combined_mutation = False
            changed = not replay and domain_rows(c) != before
            if fault == 'before_revision':
                raise RuntimeError('synthetic fault before revision')
            if changed:
                if revision >= _MAX_REVISION:
                    raise RuntimeError('revision capacity exceeded')
                c.execute('UPDATE fixture_revision SET revision=revision+1')
                revision += 1
            if fault is True or fault == 'after_revision':
                raise RuntimeError('synthetic fault after mutation and revision')
            return result, revision, changed
        return self.auth.bank._transaction(operation)
