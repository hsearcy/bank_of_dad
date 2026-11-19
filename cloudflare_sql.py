"""Small sqlite3-shaped adapter for synchronous Durable Object SQL.

Only the enclosing transactionSync commits. Exceptions always reach it; route
finally/close and domain commit() cannot split a financial operation.
"""
import math
import sqlite3

MAX_SAFE_INTEGER = 2**53 - 1


def exact(value):
    if isinstance(value, int) and abs(value) > MAX_SAFE_INTEGER:
        raise ValueError('Integer exceeds the exact Cloudflare SQL bridge range')
    if isinstance(value, float) and (not math.isfinite(value) or abs(value) > MAX_SAFE_INTEGER):
        raise ValueError('Unsafe database number')
    return value


class Cursor:
    def __init__(self, rows, lastrowid=None):
        self.rows = rows
        self.lastrowid = lastrowid
        self.offset = 0

    def fetchone(self):
        if self.offset == len(self.rows):
            return None
        row = self.rows[self.offset]
        self.offset += 1
        return row

    def fetchall(self):
        rows = self.rows[self.offset:]
        self.offset = len(self.rows)
        return rows

    def __iter__(self):
        return iter(self.fetchall())


class Connection:
    def __init__(self, sql):
        self.sql = sql
        self.in_transaction = False
        self.failed = False

    def execute(self, statement, params=()):
        if not self.in_transaction:
            raise RuntimeError('SQL outside the family transaction')
        try:
            rows = self.sql.exec(statement, *(exact(p) for p in params)).toArray()
            if hasattr(rows, 'to_py'):
                rows = rows.to_py()
            for row in rows:
                for value in row.values():
                    exact(value)
            lastrowid = None
            if statement.lstrip().upper().startswith('INSERT'):
                ids = self.sql.exec('SELECT last_insert_rowid() AS id').toArray()
                if hasattr(ids, 'to_py'):
                    ids = ids.to_py()
                lastrowid = ids[0]['id']
                exact(lastrowid)
            return Cursor(rows, lastrowid)
        except Exception:
            self.failed = True
            raise

    def executescript(self, script):
        statement = ''
        for line in script.splitlines(keepends=True):
            statement += line
            if sqlite3.complete_statement(statement):
                self.execute(statement)
                statement = ''
        if statement.strip():
            raise ValueError('Incomplete schema statement')

    def commit(self):
        if not self.in_transaction or self.failed:
            raise RuntimeError('Invalid family transaction')

    def close(self):
        # Routes close their borrowed connection; the DO owns its lifetime.
        pass
