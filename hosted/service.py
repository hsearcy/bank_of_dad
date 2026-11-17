"""Internal-only, per-object storage service. Caller must authenticate/authorize.

Each instance must receive exactly one family's DO storage, never shared storage.
No RPC or HTTP transport is provided here.
"""
import inspect
import functools
import json
import math
import sqlite3

import bank
import config
import db
from .adapter import Connection

_MUTATIONS = {
    'add_kid': bank.add_kid, 'gift': bank.gift,
    'save': bank.move_to_savings, 'withdraw': bank.withdraw_savings,
    'spend': bank.spend, 'invest': bank.buy_bond,
    'collect_rewards': bank.collect_rewards,
}
_READS = {'kids': bank.all_kids_state, 'transactions': bank.transactions}
_VERSION = 1
# Deliberately far below JavaScript's 2**53 exact-integer limit.
MAX_CENTS = 1_000_000_000
MAX_ROWS = 1000
MAX_KIDS = 100
MAX_HISTORY_READ = 1000
MAX_TRANSACTIONS_READ = 100
MAX_TIMESTAMP = 4_000_000_000


def _proxy(callback):
    from pyodide.ffi import create_proxy
    return create_proxy(callback)


def _poison_on_error(method):
    """A caught application error must not allow an active transaction to commit."""
    @functools.wraps(method)
    def wrapped(self, *args, **kwargs):
        conn = self.conn if isinstance(self, FamilyService) else self.bank.conn
        try:
            return method(self, *args, **kwargs)
        except BaseException:
            if conn._active:
                conn._poisoned = True
            raise
    return wrapped


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
        self._combined_mutation = False

    def _transaction(self, operation):
        if self.conn._active:
            self.conn._poisoned = True
            raise RuntimeError('nested transaction forbidden')
        def run():
            if self.conn._active:
                raise RuntimeError('nested transaction forbidden')
            self.conn._active = True
            self.conn._poisoned = False
            try:
                result = operation(self.conn)
                if self.conn._poisoned:
                    raise RuntimeError('transaction poisoned')
                return result
            finally:
                self.conn._capability = None
                self.conn._active = False
        proxy = self.proxy_factory(run)
        try:
            return self.storage.transactionSync(proxy)
        finally:
            destroy = getattr(proxy, 'destroy', None)
            if destroy:
                destroy()

    @_poison_on_error
    def _initialize(self, conn):
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view','trigger','index') AND name NOT LIKE 'sqlite_%'").fetchall()
        names = {row['name'] for row in tables}
        if 'hosted_schema' not in names:
            if names:
                raise RuntimeError('unversioned nonempty database requires explicit migration')
            conn.execute('CREATE TABLE hosted_schema (version INTEGER NOT NULL)')
            conn.execute('INSERT INTO hosted_schema(version) VALUES (1)')
            _script(conn, db.SCHEMA)
            db._migrate(conn)
            conn.execute('CREATE TABLE hosted_operations (operation_id TEXT PRIMARY KEY, payload TEXT NOT NULL, result TEXT NOT NULL)')
            return
        versions = conn.execute('SELECT version FROM hosted_schema').fetchall()
        if len(versions) != 1 or type(versions[0]['version']) is not int:
            raise RuntimeError('invalid schema marker')
        version = versions[0]['version']
        if version != _VERSION:
            raise RuntimeError('unsupported schema version')

    def initialize(self):
        return self._transaction(self._initialize)

    @staticmethod
    def _validate(table, action, args):
        if not isinstance(action, str) or action not in table or not isinstance(args, list):
            raise ValueError('unsupported action or arguments')
        try:
            bound = inspect.signature(table[action]).bind(None, *args)
        except TypeError as exc:
            raise ValueError('invalid arguments') from exc
        bound.apply_defaults()
        values = dict(bound.arguments)
        values.pop('conn')
        for key in ('kid_id', 'amount_cents', 'limit'):
            if key in values and (type(values[key]) is not int or not 0 < values[key] <= (MAX_TRANSACTIONS_READ if key == 'limit' else MAX_CENTS)):
                raise ValueError('invalid integer argument: ' + key)
        for key in ('name', 'emoji', 'color', 'note', 'reason', 'term_key'):
            if key in values and (not isinstance(values[key], str) or len(values[key]) > 500):
                raise ValueError('invalid text argument: ' + key)
        if 'name' in values and not values['name'].strip():
            raise ValueError('name required')
        if 'kind' in values and (not isinstance(values['kind'], str) or values['kind'] not in ('all', 'allowance', 'interest', 'invest')):
            raise ValueError('invalid reward kind')
        # Canonicalize argument defaults, but preserve text exactly (including whitespace).
        return json.dumps([action, list(values.values())], ensure_ascii=False, allow_nan=False, separators=(',', ':'))

    def _check_state(self, conn):
        """Check SQL values before the SQLite-to-JS bridge can round them.

        Row caps bound all intermediate sums well below 2**53. Check again
        after a mutation inside the same transaction so violations roll back.
        """
        def reject_if(sql):
            if conn.execute(sql).fetchone()['bad']:
                raise ValueError('hosted state exceeds safe limits')

        def bounded_number(value, low, high):
            return type(value) in (int, float) and math.isfinite(value) and low <= value <= high
        if (type(config.STARTING_WALLET_CENTS) is not int or not 0 <= config.STARTING_WALLET_CENTS <= MAX_CENTS
            or type(config.ALLOWANCE_CENTS) is not int or not 0 <= config.ALLOWANCE_CENTS <= MAX_CENTS
            or type(config.ALLOWANCE_PERIOD_DAYS) is not int or not 1 <= config.ALLOWANCE_PERIOD_DAYS <= 3650
            or not bounded_number(config.SAVINGS_WEEKLY_RATE, 0, 1)):
            raise ValueError('hosted state exceeds safe limits')
        for spec in config.BONDS.values():
            if (type(spec['days']) is not int or not 1 <= spec['days'] <= 3650
                or not bounded_number(spec['return_pct'], 0, 100)):
                raise ValueError('hosted state exceeds safe limits')

        for table, maximum in (('kids', MAX_KIDS), ('bonds', MAX_ROWS),
                               ('history', MAX_HISTORY_READ), ('transactions', MAX_ROWS)):
            reject_if(f'SELECT EXISTS(SELECT 1 FROM {table} LIMIT 1 OFFSET {maximum}) AS bad')
        def integer(col, low=0, high=MAX_CENTS, nullable=False):
            bad = f"(typeof({col}) != 'integer' OR {col} < {low} OR {col} > {high})"
            return f'({col} IS NOT NULL AND {bad})' if nullable else bad
        def timestamp(col):
            return f"(typeof({col}) NOT IN ('integer','real') OR NOT ({col} BETWEEN 0 AND {MAX_TIMESTAMP}))"
        checks = {
            'kids': [integer('id', 1), integer('wallet_cents'), integer('savings_cents'),
                     integer('allowance_cents', nullable=True), integer('allowance_period_days', 1, 3650, True),
                     integer('allowance_periods_paid', 0, 100000), integer('allowance_enabled', 0, 1),
                     *[timestamp(c) for c in ('allowance_anchor_ts', 'savings_accrued_ts', 'created_ts')]],
            'bonds': [integer('id', 1), integer('kid_id', 1), integer('principal_cents'),
                      integer('payout_cents'), 'payout_cents < principal_cents', integer('collected', 0, 1),
                      timestamp('buy_ts'), timestamp('mature_ts'), 'mature_ts < buy_ts'],
            'transactions': [integer('id', 1), integer('kid_id', 1), integer('amount_cents', -MAX_CENTS),
                             integer('bond_id', 1, MAX_CENTS, True), timestamp('ts')],
            'history': [integer('id', 1), integer('kid_id', 1), integer('networth_cents'),
                        *[integer(c, nullable=True) for c in ('wallet_cents', 'savings_cents', 'invest_cents')], timestamp('ts')],
        }
        for table, predicates in checks.items():
            reject_if(f"SELECT EXISTS(SELECT 1 FROM {table} WHERE {' OR '.join(predicates)}) AS bad")
        # Enforce bounded rates and offset even for externally created state.
        for row in conn.execute('SELECT key, value FROM settings').fetchall():
            key, value = row['key'], row['value']
            if key == 'time_offset' or key == 'savings_weekly_pct' or key.startswith('return_pct_'):
                try:
                    number = float(value)
                except (ValueError, TypeError, OverflowError):
                    raise ValueError('hosted state exceeds safe limits') from None
                maximum = 0 if key == 'time_offset' else 100
                if not math.isfinite(number) or not 0 <= number <= maximum:
                    raise ValueError('hosted state exceeds safe limits')
        now = bank.now_ts(conn)
        if not math.isfinite(now) or not 0 <= now <= MAX_TIMESTAMP:
            raise ValueError('hosted state exceeds safe limits')
        # Values read here were SQL-checked; at most 1000 bounded bonds and
        # timestamps/rates above bound every intermediate far below 2**53.
        for kid in bank.all_kids_state(conn):
            for key in ('wallet_cents', 'savings_cents', 'invest_cents', 'owned_cents',
                        'pending_cents', 'pending_allowance_cents', 'pending_interest_cents',
                        'pending_bonds_cents'):
                if type(kid[key]) is not int or not 0 <= kid[key] <= MAX_CENTS:
                    raise ValueError('hosted state exceeds safe limits')

    @staticmethod
    def _require_child(conn, action, args):
        if action not in ('add_kid', 'kids'):
            if not conn.execute('SELECT 1 AS present FROM kids WHERE id=?', (args[0],)).fetchone():
                raise ValueError('invalid request')

    @_poison_on_error
    def _mutate(self, conn, operation_id, action, args):
        if not isinstance(operation_id, str) or not operation_id or len(operation_id) > 200:
            raise ValueError('valid operation ID required')
        payload = self._validate(_MUTATIONS, action, args)
        self._initialize(conn)
        # The combined fixture must exclusively enter through its recovery and
        # revision coordinator. This is a misuse guard, not a Python sandbox.
        marker = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='local_auth_schema'").fetchone()
        combined = False
        if marker:
            versions = conn.execute('SELECT version FROM local_auth_schema').fetchall()
            combined = any(row['version'] == 3 for row in versions)
        if combined and not self._combined_mutation:
            raise RuntimeError('combined family requires recovery revision coordinator')
        self._check_state(conn)
        self._require_child(conn, action, args)
        row = conn.execute('SELECT payload, result FROM hosted_operations WHERE operation_id=?', (operation_id,)).fetchone()
        if row:
            if row['payload'] != payload:
                raise ValueError('operation ID reused with different payload')
            return json.loads(row['result'])
        result = _MUTATIONS[action](conn, *args)
        self._check_state(conn)
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        conn.execute('INSERT INTO hosted_operations(operation_id, payload, result) VALUES(?,?,?)', (operation_id, payload, encoded))
        return json.loads(encoded)

    def mutate(self, operation_id, action, args):
        """Synthetic test helper only; never expose this service as an RPC."""
        def op(conn):
            with conn._authorized_bank():
                return self._mutate(conn, operation_id, action, args)
        return self._transaction(op)

    @_poison_on_error
    def _read(self, conn, action, args):
        self._validate(_READS, action, args)
        self._initialize(conn)
        self._check_state(conn)
        self._require_child(conn, action, args)
        return _READS[action](conn, *args)

    def read(self, action, args):
        return self._transaction(lambda conn: self._read(conn, action, args))
