"""Internal singleton directory. Trusted callers only; no HTTP interface.

Identity dictionaries are assertions from a future *verified* OIDC boundary,
not credentials. Never pass untrusted request data as these assertions.
"""
import hashlib
import re
import secrets
import time
from urllib.parse import urlsplit

from .adapter import Connection
from .service import _proxy

_TOKEN = re.compile(r'^[A-Za-z0-9_-]{43}$')
_FAMILY = re.compile(r'^[a-f0-9]{32}$')


def _principal(value):
    if not isinstance(value, dict) or set(value) != {'issuer', 'subject'}:
        raise ValueError('invalid verified principal')
    issuer, subject = value['issuer'], value['subject']
    if not isinstance(issuer, str) or not isinstance(subject, str) or not subject or len(subject) > 255 or len(issuer) > 2048:
        raise ValueError('invalid verified principal')
    try:
        u = urlsplit(issuer)
        if u.scheme != 'https' or not u.hostname or u.username is not None or u.password is not None or u.query or u.fragment or any(ord(c) <= 32 or ord(c) == 127 for c in issuer + subject):
            raise ValueError()
        _ = u.port
    except ValueError:
        raise ValueError('invalid verified principal') from None
    return issuer, subject


def _digest(token):
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        raise PermissionError('denied')
    return hashlib.sha256(token.encode('ascii')).hexdigest()


def _family(value):
    if not isinstance(value, str) or not _FAMILY.fullmatch(value):
        raise PermissionError('denied')
    return value


class Directory:
    def __init__(self, storage, proxy_factory=_proxy, clock=time.time):
        self.storage = storage
        self.conn = Connection(storage.sql)
        self.proxy_factory = proxy_factory
        self.clock = clock

    def _transaction(self, operation):
        if self.conn._active: raise RuntimeError('nested transaction forbidden')
        def run():
            if self.conn._active: raise RuntimeError('nested transaction forbidden')
            self.conn._active = True
            try:
                return operation(self.conn)
            finally:
                self.conn._active = False
        proxy = self.proxy_factory(run)
        try:
            return self.storage.transactionSync(proxy)
        finally:
            destroy = getattr(proxy, 'destroy', None)
            if destroy: destroy()

    def _initialize(self, c):
        names = {r['name'] for r in c.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view','trigger','index') AND name NOT LIKE 'sqlite_%'").fetchall()}
        if 'directory_schema' not in names:
            if names: raise RuntimeError('unversioned directory')
            c.execute('CREATE TABLE directory_schema (version INTEGER NOT NULL)')
            c.execute('INSERT INTO directory_schema VALUES (1)')
            c.execute('CREATE TABLE families (id TEXT PRIMARY KEY)')
            c.execute("CREATE TABLE memberships (family TEXT NOT NULL, issuer TEXT NOT NULL, subject TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('owner','parent')), status TEXT NOT NULL CHECK(status IN ('active','disabled')), PRIMARY KEY(family,issuer,subject))")
            c.execute('CREATE TABLE sessions (digest TEXT PRIMARY KEY, issuer TEXT NOT NULL, subject TEXT NOT NULL, expires INTEGER NOT NULL, revoked INTEGER NOT NULL)')
            c.execute('CREATE TABLE invitations (digest TEXT PRIMARY KEY, family TEXT NOT NULL, role TEXT NOT NULL, expires INTEGER NOT NULL, used INTEGER NOT NULL)')
        else:
            versions = c.execute('SELECT version FROM directory_schema').fetchall()
            if len(versions) != 1 or versions[0]['version'] != 1: raise RuntimeError('unsupported directory schema')

    def _run(self, fn):
        def operation(c):
            self._initialize(c)
            return fn(c)
        return self._transaction(operation)

    def _now(self):
        t = self.clock()
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not 0 <= t <= 4000000000:
            raise RuntimeError('invalid clock')
        return int(t)

    def _new_session(self, c, principal, now):
        token = secrets.token_urlsafe(32)
        c.execute('INSERT INTO sessions VALUES (?, ?, ?, ?, 0)', (_digest(token), *principal, now + 43200))
        return token

    def _auth(self, c, token, family, now, owner=False):
        digest = _digest(token)
        family = _family(family)
        row = c.execute("SELECT m.role, m.status FROM sessions s JOIN memberships m ON s.issuer=m.issuer AND s.subject=m.subject WHERE s.digest=? AND s.revoked=0 AND s.expires>? AND m.family=? AND m.status='active'", (digest, now, family)).fetchone()
        if not row or row['role'] not in ('owner', 'parent') or row['status'] != 'active' or (owner and row['role'] != 'owner'): raise PermissionError('denied')
        return row

    def create_family(self, verified_principal):
        principal = _principal(verified_principal)
        def op(c):
            family = secrets.token_hex(16)
            c.execute('INSERT INTO families VALUES (?)', (family,))
            c.execute("INSERT INTO memberships VALUES (?, ?, ?, 'owner', 'active')", (family, *principal))
            return [family, self._new_session(c, principal, self._now())]
        return self._run(op)

    def issue_session(self, verified_principal):
        principal = _principal(verified_principal)
        def op(c):
            row = c.execute("SELECT 1 AS ok FROM memberships WHERE issuer=? AND subject=? AND status='active' LIMIT 1", principal).fetchone()
            if not row: raise PermissionError('denied')
            return self._new_session(c, principal, self._now())
        return self._run(op)

    def authorize(self, session, family):
        return self._run(lambda c: self._auth(c, session, family, self._now()))

    def my_memberships(self, session):
        digest = _digest(session)
        def op(c):
            now = self._now()
            s = c.execute('SELECT issuer, subject FROM sessions WHERE digest=? AND revoked=0 AND expires>?', (digest, now)).fetchone()
            if not s: raise PermissionError('denied')
            rows = c.execute("SELECT family, role, status FROM memberships WHERE issuer=? AND subject=? AND status='active' ORDER BY family", (s['issuer'], s['subject'])).fetchall()
            if any(r['role'] not in ('owner', 'parent') or r['status'] != 'active' or not _FAMILY.fullmatch(r['family']) for r in rows): raise RuntimeError('invalid membership')
            return rows
        return self._run(op)

    def members(self, session, family):
        def op(c):
            self._auth(c, session, family, self._now())
            return c.execute('SELECT issuer, subject, role, status FROM memberships WHERE family=? ORDER BY issuer, subject', (family,)).fetchall()
        return self._run(op)

    def revoke(self, session):
        digest = _digest(session)
        def op(c):
            row = c.execute('SELECT revoked, expires FROM sessions WHERE digest=?', (digest,)).fetchone()
            if not row or row['revoked'] or row['expires'] <= self._now(): raise PermissionError('denied')
            c.execute('UPDATE sessions SET revoked=1 WHERE digest=?', (digest,))
            return True
        return self._run(op)

    def invite(self, session, family, role, ttl):
        if role != 'parent' or type(ttl) is not int or not 1 <= ttl <= 86400: raise ValueError('invalid invitation')
        def op(c):
            now = self._now()
            self._auth(c, session, family, now, owner=True)
            token = secrets.token_urlsafe(32)
            c.execute('INSERT INTO invitations VALUES (?, ?, ?, ?, 0)', (_digest(token), family, role, now + ttl))
            return token
        return self._run(op)

    def accept_invite(self, verified_principal, token):
        principal = _principal(verified_principal)
        digest = _digest(token)
        def op(c):
            now = self._now()
            row = c.execute('SELECT family, role FROM invitations WHERE digest=? AND used=0 AND expires>?', (digest, now)).fetchone()
            if not row: raise PermissionError('denied')
            if c.execute('SELECT 1 AS ok FROM memberships WHERE family=? AND issuer=? AND subject=?', (row['family'], *principal)).fetchone(): raise PermissionError('denied')
            c.execute('UPDATE invitations SET used=1 WHERE digest=?', (digest,))
            c.execute("INSERT INTO memberships VALUES (?, ?, ?, ?, 'active')", (row['family'], *principal, row['role']))
            return self._new_session(c, principal, now)
        return self._run(op)
