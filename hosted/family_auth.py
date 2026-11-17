"""Experimental co-located authorization. Trusted internal calls only, no transport.

All asserted identities and time originate at a future trusted verification boundary.
This module is not connected to Directory or FamilyBank RPC.
"""
import secrets
import time

from .directory import _principal, _family, _digest
from .service import FamilyService, _proxy

MAX_TTL = 43200
MAX_GRANTS = 1000
_SCHEMA = {
    'local_auth_schema': 'CREATE TABLE local_auth_schema (version INTEGER NOT NULL, family TEXT NOT NULL)',
    'local_members': "CREATE TABLE local_members (issuer TEXT NOT NULL, subject TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('owner','parent')), status TEXT NOT NULL CHECK(status IN ('active','disabled')), PRIMARY KEY(issuer, subject))",
    'local_grants': 'CREATE TABLE local_grants (digest TEXT PRIMARY KEY, issuer TEXT NOT NULL, subject TEXT NOT NULL, expires INTEGER NOT NULL, revoked INTEGER NOT NULL CHECK(revoked IN (0,1)))',
    'local_operations': 'CREATE TABLE local_operations (operation_id TEXT PRIMARY KEY, issuer TEXT NOT NULL, subject TEXT NOT NULL)',
}
_CATALOG = {('table', n, n, sql) for n, sql in _SCHEMA.items()} | {
    ('index', 'sqlite_autoindex_' + n + '_1', n, '') for n in ('local_members', 'local_grants', 'local_operations')
}


class FamilyAuthorization:
    def __init__(self, storage, proxy_factory=_proxy, clock=time.time):
        self.bank = FamilyService(storage, proxy_factory)
        self.clock = clock

    def _now(self):
        value = self.clock()
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 4000000000:
            raise RuntimeError('invalid clock')
        return int(value)

    def _check(self, c, family):
        _family(family)
        rows = c.execute("SELECT type, name, tbl_name, COALESCE(sql, '') AS sql FROM sqlite_master WHERE type IN ('table','view','trigger','index')").fetchall()
        actual = {(r['type'], r['name'], r['tbl_name'], r['sql']) for r in rows
                  if r['name'].startswith('local_') or r['name'].startswith('sqlite_autoindex_local_') or r['tbl_name'].startswith('local_')}
        if actual != _CATALOG:
            raise RuntimeError('invalid local authorization schema')
        marker = c.execute('SELECT version, family FROM local_auth_schema').fetchall()
        if len(marker) != 1 or type(marker[0]['version']) is not int or marker[0]['version'] != 1 or marker[0]['family'] != family:
            raise PermissionError('denied')

    def bootstrap_privileged(self, family, verified_owner):
        """Create an empty family exactly once. Never expose to an untrusted caller."""
        _family(family)
        owner = _principal(verified_owner)
        def op(c):
            rows = c.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
            if rows:
                raise RuntimeError('bootstrap requires empty database; no migration available')
            self.bank._initialize(c)
            for sql in _SCHEMA.values(): c.execute(sql)
            c.execute('INSERT INTO local_auth_schema VALUES (1, ?)', (family,))
            c.execute("INSERT INTO local_members VALUES (?, ?, 'owner', 'active')", owner)
            self._check(c, family)
        return self.bank._transaction(op)

    def _auth(self, c, family, token, owner=False):
        self._check(c, family)
        row = c.execute("SELECT m.issuer, m.subject, m.role, m.status FROM local_grants g JOIN local_members m ON m.issuer=g.issuer AND m.subject=g.subject WHERE g.digest=? AND g.revoked=0 AND g.expires>?", (_digest(token), self._now())).fetchone()
        if not row or row['status'] != 'active' or row['role'] not in ('owner', 'parent') or (owner and row['role'] != 'owner'):
            raise PermissionError('denied')
        return row['issuer'], row['subject']

    def issue_grant_privileged(self, family, verified_principal, ttl):
        """Future verified identity boundary only; directory sessions are not accepted."""
        principal = _principal(verified_principal)
        if type(ttl) is not int or not 1 <= ttl <= MAX_TTL: raise ValueError('invalid ttl')
        def op(c):
            self._check(c, family)
            row = c.execute('SELECT role, status FROM local_members WHERE issuer=? AND subject=?', principal).fetchone()
            if not row or row['status'] != 'active' or row['role'] not in ('owner', 'parent'): raise PermissionError('denied')
            now = self._now()
            c.execute('DELETE FROM local_grants WHERE revoked=1 OR expires<=?', (now,))
            if c.execute('SELECT count(*) AS n FROM local_grants').fetchone()['n'] >= MAX_GRANTS:
                raise ValueError('grant capacity exceeded')
            token = secrets.token_urlsafe(32)
            c.execute('INSERT INTO local_grants VALUES (?, ?, ?, ?, 0)', (_digest(token), *principal, now + ttl))
            return token
        return self.bank._transaction(op)

    def set_member(self, family, token, principal, role, status):
        principal = _principal(principal)
        if role not in ('owner', 'parent') or status not in ('active', 'disabled'): raise ValueError('invalid membership')
        def op(c):
            self._auth(c, family, token, owner=True)
            c.execute('INSERT INTO local_members VALUES (?, ?, ?, ?) ON CONFLICT(issuer, subject) DO UPDATE SET role=excluded.role, status=excluded.status', (*principal, role, status))
            if status == 'disabled':
                c.execute('UPDATE local_grants SET revoked=1 WHERE issuer=? AND subject=?', principal)
        return self.bank._transaction(op)

    def revoke(self, family, token):
        def op(c):
            self._auth(c, family, token)
            c.execute('UPDATE local_grants SET revoked=1 WHERE digest=?', (_digest(token),))
        return self.bank._transaction(op)

    def read(self, family, token, action, args):
        def op(c):
            self._auth(c, family, token)
            return self.bank._read(c, action, args)
        return self.bank._transaction(op)

    def mutate(self, family, token, operation_id, action, args):
        def op(c):
            issuer, subject = self._auth(c, family, token)
            if not isinstance(operation_id, str) or not operation_id or len(operation_id) > 200: raise ValueError('valid operation ID required')
            prior = c.execute('SELECT issuer, subject FROM local_operations WHERE operation_id=?', (operation_id,)).fetchone()
            if prior and (prior['issuer'], prior['subject']) != (issuer, subject): raise PermissionError('denied')
            existing = c.execute('SELECT operation_id FROM hosted_operations WHERE operation_id=?', (operation_id,)).fetchone()
            if bool(prior) != bool(existing): raise RuntimeError('invalid operation ownership')
            result = self.bank._mutate(c, operation_id, action, args)
            if not prior:
                c.execute('INSERT INTO local_operations VALUES (?, ?, ?)', (operation_id, issuer, subject))
            return result
        return self.bank._transaction(op)
