"""Internal synthetic recovery protocol; no transport or production identity boundary.

A fresh v2 family only. Never instantiate alongside v1 authorization on the same
family: its schema check deliberately rejects this schema and vice versa.
"""
import json

from .directory import _family, _principal
from .family_auth import FamilyAuthorization, _CATALOG, _SCHEMA
from .service import _MUTATIONS, _poison_on_error

_RECOVERY_TABLE = "CREATE TABLE local_recovery (operation_id TEXT PRIMARY KEY, issuer TEXT NOT NULL, subject TEXT NOT NULL, acknowledged INTEGER NOT NULL CHECK(acknowledged IN (0,1)))"
_RECOVERY_INDEX = "CREATE UNIQUE INDEX local_recovery_pending ON local_recovery(issuer, subject) WHERE acknowledged=0"
_RECOVERY_CATALOG = _CATALOG | {
    ('table', 'local_recovery', 'local_recovery', _RECOVERY_TABLE),
    ('index', 'sqlite_autoindex_local_recovery_1', 'local_recovery', ''),
    ('index', 'local_recovery_pending', 'local_recovery', _RECOVERY_INDEX),
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


class RecoveryAuthorization(FamilyAuthorization):
    """All methods are internal, synchronous and family-local; no RPC exposed."""

    @_poison_on_error
    def _check(self, c, family):
        _family(family)
        rows = c.execute("SELECT type, name, tbl_name, COALESCE(sql, '') AS sql FROM sqlite_master WHERE type IN ('table','view','trigger','index')").fetchall()
        actual = {(r['type'], r['name'], r['tbl_name'], r['sql']) for r in rows
                  if r['name'].startswith('local_') or r['name'].startswith('sqlite_autoindex_local_') or r['tbl_name'].startswith('local_')}
        if actual != _RECOVERY_CATALOG:
            raise RuntimeError('invalid local authorization schema')
        marker = c.execute('SELECT version, family FROM local_auth_schema').fetchall()
        if len(marker) != 1 or type(marker[0]['version']) is not int or marker[0]['version'] != 2 or marker[0]['family'] != family:
            raise PermissionError('denied')

    @_poison_on_error
    def bootstrap_privileged(self, family, verified_owner):
        _family(family)
        owner = _principal(verified_owner)
        def op(c):
            if c.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall():
                raise RuntimeError('bootstrap requires empty database; no migration available')
            self.bank._initialize(c)
            for sql in _SCHEMA.values():
                c.execute(sql)
            c.execute(_RECOVERY_TABLE)
            c.execute(_RECOVERY_INDEX)
            c.execute('INSERT INTO local_auth_schema VALUES (2, ?)', (family,))
            c.execute("INSERT INTO local_members VALUES (?, ?, 'owner', 'active')", owner)
            self._check(c, family)
        return self.bank._transaction(op)

    def _outcome(self, c, issuer, subject, operation_id=None):
        sql = ("SELECT r.operation_id, r.acknowledged, o.payload, o.result FROM local_recovery r "
               "JOIN local_operations l ON l.operation_id=r.operation_id AND l.issuer=r.issuer AND l.subject=r.subject "
               "JOIN hosted_operations o ON o.operation_id=r.operation_id WHERE r.issuer=? AND r.subject=?")
        args = [issuer, subject]
        if operation_id is None:
            sql += ' AND r.acknowledged=0'
        else:
            sql += ' AND r.operation_id=?'
            args.append(operation_id)
        row = c.execute(sql, tuple(args)).fetchone()
        if row is None:
            return None
        return {'operation_id': row['operation_id'], 'request': json.loads(row['payload']),
                'result': json.loads(row['result']), 'acknowledged': bool(row['acknowledged'])}

    def pending(self, family, token):
        def op(c):
            issuer, subject = self._auth(c, family, token)
            return self._outcome(c, issuer, subject)
        return self.bank._transaction(op)

    def outcome(self, family, token, operation_id):
        def op(c):
            issuer, subject = self._auth(c, family, token)
            self._valid_id(operation_id)
            return self._outcome(c, issuer, subject, operation_id)
        return self.bank._transaction(op)

    @staticmethod
    def _valid_id(operation_id):
        if not isinstance(operation_id, str) or not operation_id or len(operation_id) > 200:
            raise ValueError('valid operation ID required')

    def acknowledge(self, family, token, operation_id, result):
        def op(c):
            issuer, subject = self._auth(c, family, token)
            self._valid_id(operation_id)
            outcome = self._outcome(c, issuer, subject, operation_id)
            if outcome is None:
                raise ValueError('unknown operation')
            if _json(outcome['result']) != _json(result):
                raise ValueError('result mismatch')
            c.execute('UPDATE local_recovery SET acknowledged=1 WHERE operation_id=? AND issuer=? AND subject=?', (operation_id, issuer, subject))
            return True
        return self.bank._transaction(op)

    @_poison_on_error
    def _mutate_in_transaction(self, c, family, token, operation_id, action, args):
        # Override the v1 helper: inheriting it would silently bypass recovery.
        if c is not self.bank.conn or not c._active:
            raise RuntimeError('active bank transaction required')
        if type(self) is not RecoveryAuthorization and not self.bank._combined_mutation:
            raise RuntimeError('recovery coordinator required')
        issuer, subject = self._auth(c, family, token)
        self._valid_id(operation_id)
        payload = self.bank._validate(_MUTATIONS, action, args)
        prior = c.execute('SELECT issuer, subject FROM local_operations WHERE operation_id=?', (operation_id,)).fetchone()
        recovery = c.execute('SELECT issuer, subject FROM local_recovery WHERE operation_id=?', (operation_id,)).fetchone()
        existing = c.execute('SELECT payload, result FROM hosted_operations WHERE operation_id=?', (operation_id,)).fetchone()
        if any((prior, recovery, existing)):
            if not all((prior, recovery, existing)) or (prior['issuer'], prior['subject']) != (recovery['issuer'], recovery['subject']):
                raise RuntimeError('invalid operation ownership')
            if (prior['issuer'], prior['subject']) != (issuer, subject):
                raise PermissionError('denied')
            if existing['payload'] != payload:
                raise ValueError('operation ID reused with different payload')
            return json.loads(existing['result']), True
        if c.execute('SELECT operation_id FROM local_recovery WHERE issuer=? AND subject=? AND acknowledged=0', (issuer, subject)).fetchone():
            raise ValueError('unacknowledged operation')
        # Only the exact recovery-only implementation may enable this guard.
        # The combined subclass enables it in its outer revision coordinator.
        recovery_only = type(self) is RecoveryAuthorization
        if recovery_only:
            self.bank._combined_mutation = True
        try:
            with c._authorized_bank():
                result = self.bank._mutate(c, operation_id, action, args)
        finally:
            if recovery_only:
                self.bank._combined_mutation = False
        c.execute('INSERT INTO local_operations VALUES (?, ?, ?)', (operation_id, issuer, subject))
        c.execute('INSERT INTO local_recovery VALUES (?, ?, ?, 0)', (operation_id, issuer, subject))
        return result, False

    def mutate(self, family, token, operation_id, action, args):
        def op(c):
            result, _ = self._mutate_in_transaction(c, family, token, operation_id, action, args)
            return result
        return self.bank._transaction(op)
