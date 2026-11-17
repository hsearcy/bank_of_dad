"""Local synthetic probe only; never deploy or expose this unauthenticated worker."""
import json
import sqlite3
from urllib.parse import urlparse
from pyodide.ffi import create_proxy
from workers import WorkerEntrypoint, DurableObject, Response
import bank, db
from fixtures import LEGACY

bank.time.time = lambda: 1700000000.0

def forbidden(*args, **kwargs):
    raise RuntimeError('filesystem database access forbidden in spike')
db.get_conn = forbidden
db.init_db = forbidden
sqlite3.connect = forbidden

class Cursor:
    def __init__(self, cursor):
        self.rows = cursor.toArray().to_py()
    def fetchone(self):
        return self.rows[0] if self.rows else None
    def fetchall(self):
        return self.rows
    def __iter__(self):
        return iter(self.rows)

class Connection:
    def __init__(self, sql, fail=False):
        self.sql, self.fail = sql, fail
    def execute(self, statement, params=()):
        if self.fail and statement.lstrip().startswith('INSERT INTO transactions'):
            raise RuntimeError('injected after wallet update')
        return Cursor(self.sql.exec(statement, *params))
    def commit(self):
        pass  # The enclosing DO transactionSync owns the commit.

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
        action = urlparse(request.url).path.strip('/')
        storage = self.ctx.storage
        conn = Connection(storage.sql, action == 'fail')
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
            raise ValueError('unknown action')
        try:
            if action in ('init', 'legacy', 'gift', 'fail'):
                proxy = create_proxy(operation)
                try:
                    result = storage.transactionSync(proxy)
                finally:
                    proxy.destroy()
            else:
                result = operation()
            return Response(json.dumps({'ok': True, 'result': result}), headers={'content-type':'application/json'})
        except Exception as exc:
            return Response(json.dumps({'ok': False, 'error': str(exc)}), status=500, headers={'content-type':'application/json'})

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        action = urlparse(request.url).path.strip('/')
        name = 'legacy-proof' if action == 'legacy' else 'gift-proof'
        stub = self.env.BANK.get(self.env.BANK.idFromName(name))
        return await stub.fetch(request.url)
