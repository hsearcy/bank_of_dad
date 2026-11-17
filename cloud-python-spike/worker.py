"""Local synthetic probe only; never deploy or expose this unauthenticated worker."""
import json
import sqlite3
from urllib.parse import urlparse
from pyodide.ffi import create_proxy
from workers import WorkerEntrypoint, DurableObject, Response
import bank, db
from fixtures import LEGACY
from scenario import perform

bank.time.time = lambda: 1700000000.0

def forbidden(*args, **kwargs):
    raise RuntimeError('filesystem database access forbidden in spike')
db.get_conn = forbidden
db.init_db = forbidden
sqlite3.connect = forbidden

class Cursor:
    def __init__(self, cursor):
        self.rows = cursor.toArray().to_py()
        self.lastrowid = None
    def fetchone(self):
        return self.rows[0] if self.rows else None
    def fetchall(self):
        return self.rows
    def __iter__(self):
        return iter(self.rows)

class Connection:
    def __init__(self, sql, fail=False):
        self.sql, self.fail, self.in_transaction = sql, fail, False
    def execute(self, statement, params=()):
        if self.fail == 'fail' and statement.lstrip().startswith('INSERT INTO transactions'):
            raise RuntimeError('injected after wallet update')
        if self.fail == 'fail_collect' and statement.lstrip().startswith('INSERT INTO history'):
            raise RuntimeError('injected after collect writes')
        result = Cursor(self.sql.exec(statement, *params))
        if statement.lstrip().startswith('INSERT INTO bonds'):
            result.lastrowid = self.sql.exec('SELECT last_insert_rowid() AS id').toArray().to_py()[0]['id']
        return result
    def commit(self):
        if not self.in_transaction:
            raise RuntimeError('commit outside transactionSync')
        # The enclosing DO transactionSync owns the commit.

def run_script(conn, script):
    statement = ''
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            conn.execute(statement)
            statement = ''
    if statement.strip():
        raise ValueError('incomplete schema statement')

def snapshot(conn):
    return {'kids': conn.execute('SELECT * FROM kids ORDER BY id').fetchall(),
            'ledger': conn.execute('SELECT * FROM transactions ORDER BY id').fetchall(),
            'history': conn.execute('SELECT * FROM history ORDER BY id').fetchall(),
            'bonds': conn.execute('SELECT * FROM bonds ORDER BY id').fetchall(),
            'settings': conn.execute('SELECT * FROM settings ORDER BY key').fetchall()}

def migration_snapshot(conn):
    return {'schema': conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name").fetchall(),
            'tables': {table: {'columns': conn.execute('PRAGMA table_info(' + table + ')').fetchall(),
                    'rows': conn.execute('SELECT * FROM ' + table + ' ORDER BY id').fetchall()}
            for table in ('kids', 'history', 'transactions')}}

class Bank(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.ctx = ctx
    async def fetch(self, request):
        action = urlparse(request.url).path.strip('/').split('/')[-1]
        storage = self.ctx.storage
        conn = Connection(storage.sql, action)
        def operation():
            if action == 'init':
                run_script(conn, db.SCHEMA)
                db._migrate(conn)
                return bank.add_kid(conn, 'Synthetic', '🐣', '#123456')
            if action == 'legacy':
                run_script(conn, LEGACY)
                before = migration_snapshot(conn)
                db._migrate(conn)
                first = migration_snapshot(conn)
                db._migrate(conn)
                return {'before': before, 'first': first, 'second': migration_snapshot(conn)}
            if action in ('gift', 'fail'):
                return bank.gift(conn, 1, 500, '  chores 🧹  ')
            if action == 'guard':
                try:
                    db.get_conn()
                except RuntimeError as exc:
                    return str(exc)
                raise AssertionError('disk DB guard failed')
            if action == 'read':
                return snapshot(conn)
            return perform(conn, action)
        try:
            if action not in ('read', 'guard', 'pending'):
                proxy = create_proxy(operation)
                try:
                    conn.in_transaction = True
                    result = storage.transactionSync(proxy)
                finally:
                    conn.in_transaction = False
                    proxy.destroy()
            else:
                result = operation()
            return Response(json.dumps({'ok': True, 'result': result}), headers={'content-type':'application/json'})
        except Exception as exc:
            return Response(json.dumps({'ok': False, 'error': str(exc)}), status=500, headers={'content-type':'application/json'})

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        parts = urlparse(request.url).path.strip('/').split('/')
        action = parts[-1]
        name = parts[0] if len(parts) == 2 else ('legacy-proof' if action == 'legacy' else 'gift-proof')
        stub = self.env.BANK.get(self.env.BANK.idFromName(name))
        return await stub.fetch(request.url)
