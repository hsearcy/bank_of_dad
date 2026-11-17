import json
import sqlite3
import unittest
from unittest.mock import patch

from hosted.service import FamilyService
from hosted.adapter import Connection

class Result:
    def __init__(self, cursor): self.cursor = cursor
    def toArray(self): return self
    def to_py(self): return [dict(row) for row in self.cursor.fetchall()]

class Storage:
    def __init__(self):
        self.db = sqlite3.connect(':memory:', isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.sql = self
    def exec(self, sql, *params): return Result(self.db.execute(sql, params))
    def transactionSync(self, callback):
        self.db.execute('BEGIN')
        try:
            value = callback()
            self.db.execute('COMMIT')
            return value
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

class FoundationTest(unittest.TestCase):
    def setUp(self):
        self.storage = Storage()
        self.svc = FamilyService(self.storage, proxy_factory=lambda cb: cb)
        self.addCleanup(self.storage.db.close)

    def test_invalid_mutation_arguments_fail_closed(self):
        self.svc.initialize()
        for action, args in [('add_kid', ['']), ('gift', [1, 1.5]),
                             ('gift', [1, -1]), ('spend', [True, 1]),
                             ('save', [1, 0]), ('invest', [1, '1mo', 1.1])]:
            with self.subTest(action=action, args=args), self.assertRaises(ValueError):
                self.svc.mutate('id', action, args)
        self.assertEqual(self.svc.read('kids', []), [])
    def test_commit_and_execute_outside_fail_closed(self):
        c = Connection(self.storage.sql)
        with self.assertRaises(RuntimeError): c.commit()
        with self.assertRaises(RuntimeError): c.execute('SELECT 1')
    def test_operations_retry_conflicts_and_rollback(self):
        self.svc.initialize()
        self.assertTrue(self.svc.mutate('a', 'add_kid', ['Kid', '🐣', '#123456']))
        self.assertTrue(self.svc.mutate('g', 'gift', [1, 500, 'x']))
        self.assertTrue(self.svc.mutate('default', 'gift', [1, 1]))
        self.assertTrue(self.svc.mutate('default', 'gift', [1, 1, 'From Dad']))
        self.assertTrue(self.svc.mutate('g', 'gift', [1, 500, 'x']))
        with self.assertRaises(ValueError): self.svc.mutate('g', 'gift', [1, 501, 'x'])
        self.assertEqual(self.svc.mutate('s', 'save', [1, 400]), [True, 'Saved!'])
        self.assertEqual(self.svc.mutate('w', 'withdraw', [1, 100]), [True, 'Done!'])
        self.assertEqual(self.svc.mutate('p', 'spend', [1, 125, 'toy']), [True, 'Spent!'])
        state = self.svc.read('kids', [])
        self.assertEqual((state[0]['wallet_cents'], state[0]['savings_cents']), (576, 300))
        before = json.dumps(state, sort_keys=True)
        def broken(conn):
            import bank
            bank.gift(conn, 1, 100, 'rollback')
            raise RuntimeError('after inner commit')
        with self.assertRaisesRegex(RuntimeError, 'after inner commit'):
            self.svc._transaction(broken)
        self.assertEqual(json.dumps(self.svc.read('kids', []), sort_keys=True), before)
        self.assertEqual(len(self.svc.read('transactions', [1])), 5)
    def test_idempotency_record_and_mutation_rollback_together(self):
        self.svc.mutate('kid', 'add_kid', ['Kid'])
        before = self.svc.read('kids', [])
        original = self.storage.exec
        def fail_after_record(sql, *params):
            result = original(sql, *params)
            if sql.startswith('INSERT INTO hosted_operations'):
                raise RuntimeError('after idempotency record')
            return result
        with patch.object(self.storage, 'exec', side_effect=fail_after_record):
            with self.assertRaisesRegex(RuntimeError, 'after idempotency record'):
                self.svc.mutate('retry', 'gift', [1, 100])
        self.assertEqual(self.svc.read('kids', []), before)
        self.assertEqual(self.svc.read('transactions', [1]), [])
        self.assertTrue(self.svc.mutate('retry', 'gift', [1, 100]))
        self.assertEqual(len(self.svc.read('transactions', [1])), 1)

    def test_version_and_family_isolation(self):
        self.svc.initialize(); self.svc.initialize()
        self.svc.mutate('a', 'add_kid', ['One'])
        other_storage = Storage()
        self.addCleanup(other_storage.db.close)
        other = FamilyService(other_storage, proxy_factory=lambda cb: cb)
        other.initialize()
        self.assertEqual(other.read('kids', []), [])
        self.storage.db.execute('UPDATE hosted_schema SET version=999')
        with self.assertRaises(RuntimeError): self.svc.initialize()
    def test_interrupted_migration_rolls_back_marker_and_schema(self):
        with patch('hosted.service.db._migrate', side_effect=RuntimeError('migration failed')):
            with self.assertRaisesRegex(RuntimeError, 'migration failed'):
                self.svc.initialize()
        self.assertEqual(self.storage.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall(), [])
        self.svc.initialize()
        self.assertEqual(self.storage.db.execute('SELECT version FROM hosted_schema').fetchone()['version'], 1)

    def test_unversioned_database_rejected(self):
        self.storage.db.execute('CREATE TABLE kids (id INTEGER)')
        with self.assertRaises(RuntimeError): self.svc.initialize()
        self.assertIsNone(self.storage.db.execute("SELECT name FROM sqlite_master WHERE name='hosted_schema'").fetchone())

if __name__ == '__main__': unittest.main()
