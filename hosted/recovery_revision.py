"""Synthetic-only fresh combined recovery/revision core; not an RPC or gateway."""
import hashlib
import json

from .directory import _family, _principal
from .family_auth import _SCHEMA
from .recovery import RecoveryAuthorization, _RECOVERY_TABLE, _RECOVERY_INDEX
from .service import _poison_on_error

_TABLE = 'CREATE TABLE fixture_revision (version INTEGER NOT NULL, revision INTEGER NOT NULL)'
_CATALOG_SHA256 = '58121acc23f2cf2115d04a45a5931e432efd1f96bef0c4fd56c6e695fae52dce'
_MAX_REVISION = 2**53 - 1


class RecoveryRevisionAuthorization(RecoveryAuthorization):
    """All writes, including the inherited public mutate, go through one transaction.

    This is a trusted-internal misuse guard, not a sandbox against Python or SQL.
    The combined auth marker is version 3, distinct from v1 and recovery-only v2.
    """
    @_poison_on_error
    def _check(self, c, family):
        _family(family)
        rows = c.execute('SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name').fetchall()
        catalog = [[r[k] for k in ('type', 'name', 'tbl_name', 'sql')] for r in rows]
        digest = hashlib.sha256(json.dumps(catalog, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        if digest != _CATALOG_SHA256:
            raise RuntimeError('unsupported combined schema; no migration available')
        marker = c.execute('SELECT version, family FROM local_auth_schema').fetchall()
        if len(marker) != 1 or type(marker[0]['version']) is not int or marker[0]['version'] != 3 or marker[0]['family'] != family:
            raise PermissionError('denied')
        self._revision(c)

    @staticmethod
    def _revision(c):
        rows = c.execute('SELECT version, revision FROM fixture_revision').fetchall()
        if (len(rows) != 1 or type(rows[0]['version']) is not int or rows[0]['version'] != 1
                or type(rows[0]['revision']) is not int or not 0 <= rows[0]['revision'] <= _MAX_REVISION):
            raise RuntimeError('invalid revision marker')
        return rows[0]['revision']

    @_poison_on_error
    def bootstrap_privileged(self, family, verified_owner):
        _family(family)
        owner = _principal(verified_owner)
        def install(c):
            if c.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall():
                raise RuntimeError('bootstrap requires empty database; no migration available')
            self.bank._initialize(c)
            for sql in _SCHEMA.values():
                c.execute(sql)
            c.execute(_RECOVERY_TABLE)
            c.execute(_RECOVERY_INDEX)
            c.execute(_TABLE)
            c.execute('INSERT INTO fixture_revision VALUES (1, 0)')
            c.execute('INSERT INTO local_auth_schema VALUES (3, ?)', (family,))
            c.execute("INSERT INTO local_members VALUES (?, ?, 'owner', 'active')", owner)
            self._check(c, family)
        return self.bank._transaction(install)

    @staticmethod
    def _domain_rows(c):
        return [c.execute('SELECT * FROM ' + table + ' ORDER BY id').fetchall()
                for table in ('kids', 'bonds', 'transactions', 'history')]

    @_poison_on_error
    def _mutate_in_transaction(self, c, family, token, operation_id, action, args):
        if c is not self.bank.conn or not c._active:
            raise RuntimeError('active bank transaction required')
        # Authenticate and validate the entire schema before any replay lookup.
        self._auth(c, family, token)
        revision = self._revision(c)
        before = self._domain_rows(c)
        self.bank._combined_mutation = True
        try:
            result, replay = super()._mutate_in_transaction(c, family, token, operation_id, action, args)
        finally:
            self.bank._combined_mutation = False
        changed = not replay and self._domain_rows(c) != before
        if changed:
            if revision >= _MAX_REVISION:
                raise RuntimeError('revision capacity exceeded')
            c.execute('UPDATE fixture_revision SET revision=revision+1')
        return result, replay

    def snapshot(self, family, token):
        def read(c):
            self._auth(c, family, token)
            return {'kids': self.bank._read(c, 'kids', []), 'revision': self._revision(c)}
        return self.bank._transaction(read)
