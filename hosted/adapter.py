"""Subset of sqlite3's connection interface required by bank.py.

All SQL, including reads, must be inside the enclosing synchronous transaction.
This is not a general DB-API adapter; only supported bank paths are exposed.
"""

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


class Connection:
    def __init__(self, sql):
        self._sql = sql
        self._active = False

    def execute(self, statement, params=()):
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
