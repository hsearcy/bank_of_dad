"""Internal-only, per-object storage service. Caller must authenticate/authorize.

Each instance must receive exactly one family's DO storage, never shared storage.
No RPC or HTTP transport is provided here.
"""
import inspect
import json
import sqlite3

import bank
import db
from .adapter import Connection

_MUTATIONS = {
    'add_kid': bank.add_kid, 'gift': bank.gift,
    'save': bank.move_to_savings, 'withdraw': bank.withdraw_savings,
    'spend': bank.spend, 'invest': bank.buy_bond,
}
_READS = {'kids': bank.all_kids_state, 'transactions': bank.transactions}
_VERSION = 1


def _proxy(callback):
    from pyodide.ffi import create_proxy
    return create_proxy(callback)


def _script(conn, script):
    statement = ''
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            conn.execute(statement)
            statement = ''
    if statement.strip():
        raise ValueError('incomplete schema')


class FamilyService:
    def __init__(self, storage, proxy_factory=_proxy):
        self.storage = storage
        self.conn = Connection(storage.sql)
        self.proxy_factory = proxy_factory

    def _transaction(self, operation):
        if self.conn._active:
            raise RuntimeError('nested transaction forbidden')
        def run():
            if self.conn._active:
                raise RuntimeError('nested transaction forbidden')
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
            if destroy:
                destroy()

    def _initialize(self, conn):
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        names = {row['name'] for row in tables}
        if 'hosted_schema' not in names:
            if names:
                raise RuntimeError('unversioned nonempty database requires explicit migration')
            conn.execute('CREATE TABLE hosted_schema (version INTEGER NOT NULL)')
            conn.execute('INSERT INTO hosted_schema(version) VALUES (0)')
        versions = conn.execute('SELECT version FROM hosted_schema').fetchall()
        if len(versions) != 1 or type(versions[0]['version']) is not int:
            raise RuntimeError('invalid schema marker')
        version = versions[0]['version']
        if version < 0 or version > _VERSION:
            raise RuntimeError('unsupported schema version')
        if version == 0:
            _script(conn, db.SCHEMA)
            db._migrate(conn)
            conn.execute('CREATE TABLE hosted_operations (operation_id TEXT PRIMARY KEY, payload TEXT NOT NULL, result TEXT NOT NULL)')
            conn.execute('UPDATE hosted_schema SET version=1')

    def initialize(self):
        return self._transaction(self._initialize)

    @staticmethod
    def _validate(table, action, args):
        if action not in table or not isinstance(args, list):
            raise ValueError('unsupported action or arguments')
        try:
            bound = inspect.signature(table[action]).bind(None, *args)
        except TypeError as exc:
            raise ValueError('invalid arguments') from exc
        bound.apply_defaults()
        values = dict(bound.arguments)
        values.pop('conn')
        for key in ('kid_id', 'amount_cents', 'limit'):
            if key in values and (type(values[key]) is not int or not 0 < values[key] <= 10**12):
                raise ValueError('invalid integer argument: ' + key)
        for key in ('name', 'emoji', 'color', 'note', 'reason', 'term_key'):
            if key in values and (not isinstance(values[key], str) or len(values[key]) > 500):
                raise ValueError('invalid text argument: ' + key)
        if 'name' in values and not values['name'].strip():
            raise ValueError('name required')
        # Normalize omitted defaults so semantically identical retries match.
        return json.dumps([action, list(values.values())], ensure_ascii=False, allow_nan=False, separators=(',', ':'))

    def mutate(self, operation_id, action, args):
        if not isinstance(operation_id, str) or not operation_id or len(operation_id) > 200:
            raise ValueError('valid operation ID required')
        payload = self._validate(_MUTATIONS, action, args)
        def apply(conn):
            self._initialize(conn)
            row = conn.execute('SELECT payload, result FROM hosted_operations WHERE operation_id=?', (operation_id,)).fetchone()
            if row:
                if row['payload'] != payload:
                    raise ValueError('operation ID reused with different payload')
                return json.loads(row['result'])
            result = _MUTATIONS[action](conn, *args)
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            conn.execute('INSERT INTO hosted_operations(operation_id, payload, result) VALUES(?,?,?)', (operation_id, payload, encoded))
            return json.loads(encoded)
        return self._transaction(apply)

    def read(self, action, args):
        self._validate(_READS, action, args)
        def apply(conn):
            self._initialize(conn)
            return _READS[action](conn, *args)
        return self._transaction(apply)
