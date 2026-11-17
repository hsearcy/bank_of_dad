"""Subset of sqlite3's connection interface required by bank.py.

All SQL, including reads, must be inside the enclosing synchronous transaction.
This is not a general DB-API adapter; only supported bank paths are exposed.
"""
from contextlib import contextmanager

class Cursor:
    def __init__(self, rows, lastrowid=None):
        self._rows = rows
        self._offset = 0
        self.lastrowid = lastrowid

    def fetchone(self):
        if self._offset == len(self._rows):
            return None
        row = self._rows[self._offset]
        self._offset += 1
        return row

    def fetchall(self):
        rows = self._rows[self._offset:]
        self._offset = len(self._rows)
        return rows

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                return
            yield row


class _BankCapability:
    """Internal misuse guard, not a sandbox or an authorization boundary.

    Trusted Python with arbitrary access can forge or bypass this object.
    """
    def __init__(self, owner):
        self.owner = owner
        self.active = True


class Connection:
    def __init__(self, sql):
        self._sql = sql
        self._active = False
        self._capability = None
        self._bank_busy = False
        self._poisoned = False

    @contextmanager
    def _authorized_bank(self):
        if not self._active or self._capability is not None:
            self._poisoned = True
            raise RuntimeError('invalid hosted bank capability')
        capability = _BankCapability(self)
        self._capability = capability
        try:
            yield
        except BaseException:
            self._poisoned = True
            raise
        finally:
            capability.active = False
            self._capability = None

    def _bank_action(self, fn, args, kwargs):
        if (not self._active or type(self._capability) is not _BankCapability
                or not self._capability.active or self._capability.owner is not self
                or self._bank_busy):
            self._poisoned = True
            if type(self._capability) is _BankCapability:
                self._capability.owner._poisoned = True
            raise RuntimeError('hosted bank action requires an authorized transaction')
        self._bank_busy = True
        try:
            return fn(self, *args, **kwargs)
        except BaseException:
            self._poisoned = True
            raise
        finally:
            self._bank_busy = False

    def execute(self, statement, params=()):
        try:
            return self._execute(statement, params)
        except BaseException:
            self._poisoned = True
            raise

    def _execute(self, statement, params):
        if not self._active:
            raise RuntimeError('SQL outside transactionSync')
        if not isinstance(params, (tuple, list)):
            raise TypeError('positional SQL parameters required')
        rows = self._sql.exec(statement, *params).toArray().to_py()
        lastrowid = None
        if statement.lstrip().upper().startswith('INSERT '):
            # Never marshal an oversized SQLite rowid through JS as a number.
            check = self._sql.exec('SELECT last_insert_rowid() BETWEEN 1 AND 1000000000 AS ok').toArray().to_py()[0]['ok']
            if not check:
                raise ValueError('hosted state exceeds safe limits')
            lastrowid = self._sql.exec('SELECT last_insert_rowid() AS id').toArray().to_py()[0]['id']
        return Cursor(rows, lastrowid)

    def commit(self):
        if not self._active:
            raise RuntimeError('commit outside transactionSync')
        # Existing bank functions commit internally; the outer transaction owns it.
