"""Unwired, local synthetic experiment. Not an RPC or a production service.

The caller must bootstrap a disposable family first, install the revision marker
once during fixture setup, and exclusively route all subsequent bank mutations
through this object. It is not a migration for any existing bank.
"""

_TABLE = 'CREATE TABLE fixture_revision (version INTEGER NOT NULL, revision INTEGER NOT NULL)'
_MAX_REVISION = 2**53 - 1


class RevisionFixture:
    def __init__(self, authorization):
        self.auth = authorization

    def install_for_fresh_fixture(self, family, token):
        def install(c):
            self.auth._auth(c, family, token)
            if c.execute("SELECT name FROM sqlite_master WHERE name='fixture_revision'").fetchone():
                raise RuntimeError('revision already exists; no migration available')
            if (c.execute('SELECT count(*) AS n FROM hosted_operations').fetchone()['n'] or
                    c.execute('SELECT count(*) AS n FROM kids').fetchone()['n']):
                raise RuntimeError('fresh empty fixture required; no migration available')
            c.execute(_TABLE)
            c.execute('INSERT INTO fixture_revision VALUES (1, 0)')
        self.auth.bank._transaction(install)

    def _revision(self, c):
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
        if action not in ('gift', 'save', 'spend'):
            raise ValueError('unsupported fixture mutation')
        def operation(c):
            self.auth._auth(c, family, token)
            revision = self._revision(c)
            result, replay = self.auth._mutate_in_transaction(c, family, token, operation_id, action, args)
            success = result[0] is True if isinstance(result, list) else result is True
            if not replay and success:
                if revision >= _MAX_REVISION:
                    raise RuntimeError('revision capacity exceeded')
                c.execute('UPDATE fixture_revision SET revision=revision+1')
                revision += 1
            if fault:
                raise RuntimeError('synthetic fault after mutation and revision')
            return result, revision, not replay and success
        return self.auth.bank._transaction(operation)
