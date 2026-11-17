import json
import sqlite3
import unittest
from unittest.mock import patch

from hosted.service import FamilyService, MAX_CENTS
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

    def test_persisted_zero_is_never_initialized(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                s = Storage()
                try:
                    s.db.execute('CREATE TABLE hosted_schema(version INTEGER NOT NULL)')
                    s.db.execute('INSERT INTO hosted_schema VALUES(0)')
                    if existing:
                        s.db.execute('CREATE TABLE legacy(value TEXT)')
                        s.db.execute("INSERT INTO legacy VALUES('keep')")
                    svc = FamilyService(s, proxy_factory=lambda cb: cb)
                    with self.assertRaisesRegex(RuntimeError, 'unsupported schema version'): svc.initialize()
                    self.assertEqual(s.db.execute('SELECT version FROM hosted_schema').fetchone()[0], 0)
                    if existing: self.assertEqual(s.db.execute('SELECT value FROM legacy').fetchone()[0], 'keep')
                    self.assertIsNone(s.db.execute("SELECT name FROM sqlite_master WHERE name='hosted_operations'").fetchone())
                finally:
                    s.db.close()

    def test_collection_validation_and_fault_rollback(self):
        self.svc.mutate('kid', 'add_kid', ['Kid'])
        for args in ([True], [1, None], [1, 'ALL'], [1, []], [1, 'all', 1], [99]):
            with self.subTest(args=args), self.assertRaises(ValueError): self.svc.mutate('bad', 'collect_rewards', args)
        import bank
        self.storage.db.execute('UPDATE kids SET allowance_anchor_ts=?', (bank.now_ts(self.storage.db)-86400*35,))
        before = self.svc.read('kids', [])
        original = self.storage.exec
        def fail_after_record(sql, *params):
            result = original(sql, *params)
            if sql.startswith('INSERT INTO hosted_operations'): raise RuntimeError('after record')
            return result
        with patch.object(self.storage, 'exec', side_effect=fail_after_record):
            with self.assertRaisesRegex(RuntimeError, 'after record'): self.svc.mutate('collect', 'collect_rewards', [1])
        self.assertEqual(self.svc.read('kids', []), before)
        self.assertEqual(self.svc.read('transactions', [1]), [])
        result = self.svc.mutate('collect', 'collect_rewards', [1])
        self.assertGreater(result, 0)
        self.assertEqual(self.svc.mutate('collect', 'collect_rewards', [1, 'all']), result)
        self.assertEqual(self.svc.mutate('again', 'collect_rewards', [1]), 0)
        with self.assertRaisesRegex(ValueError, 'different payload'): self.svc.mutate('collect', 'collect_rewards', [1, 'interest'])

    def test_bond_relational_guard_before_maturity(self):
        self.svc.mutate('kid', 'add_kid', ['Kid'])
        self.storage.db.execute("INSERT INTO bonds(kid_id,term_key,principal_cents,payout_cents,buy_ts,mature_ts,collected) VALUES(1,'1mo',100,99,1700000000,3999999999,0)")
        before = tuple(self.storage.db.execute('SELECT principal_cents,payout_cents,collected FROM bonds').fetchone())
        for action in ('read', 'collect', 'gift'):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'safe limits'):
                if action == 'read': self.svc.read('kids', [])
                elif action == 'collect': self.svc.mutate('c', 'collect_rewards', [1])
                else: self.svc.mutate('g', 'gift', [1, 1])
        self.assertEqual(tuple(self.storage.db.execute('SELECT principal_cents,payout_cents,collected FROM bonds').fetchone()), before)

    def test_collection_post_guard_rolls_back(self):
        import bank
        self.svc.mutate('kid', 'add_kid', ['Kid'])
        self.storage.db.execute('UPDATE kids SET wallet_cents=?, allowance_anchor_ts=?', (MAX_CENTS, bank.now_ts(self.storage.db)-86400*35))
        before = self.svc.read('kids', [])
        with self.assertRaisesRegex(ValueError, 'safe limits'):
            self.svc.mutate('over', 'collect_rewards', [1, 'allowance'])
        self.assertEqual(self.svc.read('kids', []), before)
        self.assertEqual(self.svc.read('transactions', [1]), [])
        self.assertIsNone(self.storage.db.execute("SELECT operation_id FROM hosted_operations WHERE operation_id='over'").fetchone())

class GuardTest(FoundationTest):
    def test_view_only_unversioned(self):
        self.storage.db.execute('CREATE VIEW orphan AS SELECT 1 AS value')
        with self.assertRaises(RuntimeError): self.svc.initialize()
        self.assertIsNone(self.storage.db.execute("SELECT name FROM sqlite_master WHERE name='hosted_schema'").fetchone())

    def test_js_precision_reproduction_and_atomic_boundary(self):
        self.svc.mutate('kid', 'add_kid', ['Kid'])
        self.storage.db.execute('UPDATE kids SET wallet_cents=9007199254740992')
        with self.assertRaisesRegex(ValueError, 'safe limits'): self.svc.mutate('overflow', 'gift', [1, 1])
        self.assertEqual(self.storage.db.execute('SELECT wallet_cents FROM kids').fetchone()[0], 9007199254740992)
        self.assertEqual(self.storage.db.execute('SELECT count(*) FROM transactions').fetchone()[0], 0)
        self.storage.db.execute('UPDATE kids SET wallet_cents=?', (MAX_CENTS-1,))
        self.assertTrue(self.svc.mutate('last', 'gift', [1, 1]))
        with self.assertRaisesRegex(ValueError, 'safe limits'): self.svc.mutate('too-far', 'gift', [1, 1])
        self.assertEqual(self.svc.read('kids', [])[0]['wallet_cents'], MAX_CENTS)
        self.assertIsNone(self.storage.db.execute("SELECT operation_id FROM hosted_operations WHERE operation_id='too-far'").fetchone())
        self.assertTrue(self.svc.mutate('last', 'gift', [1, 1]))
        self.assertEqual(self.svc.mutate('save', 'save', [1, 100]), [True, 'Saved!'])
        self.assertEqual(self.svc.mutate('withdraw', 'withdraw', [1, 100]), [True, 'Done!'])
        self.assertEqual(self.svc.mutate('spend', 'spend', [1, 1]), [True, 'Spent!'])
        self.assertEqual(self.svc.mutate('invest', 'invest', [1, '1mo', 100]), [True, 'Locked in!'])
        self.assertEqual(self.svc.read('kids', [])[0]['owned_cents'], MAX_CENTS - 1)

    def test_accrual_and_payout_limits(self):
        self.svc.mutate('kid', 'add_kid', ['Kid'])
        self.storage.db.execute('UPDATE kids SET wallet_cents=0, savings_cents=?, savings_accrued_ts=0', (MAX_CENTS,))
        with self.assertRaisesRegex(ValueError, 'safe limits'): self.svc.read('kids', [])
        with self.assertRaisesRegex(ValueError, 'safe limits'): self.svc.mutate('withdraw', 'withdraw', [1, 1])
        self.assertEqual(self.storage.db.execute('SELECT savings_cents FROM kids').fetchone()[0], MAX_CENTS)
        self.storage.db.execute('UPDATE kids SET wallet_cents=?, savings_cents=0', (MAX_CENTS,))
        with self.assertRaisesRegex(ValueError, 'safe limits'): self.svc.mutate('invest', 'invest', [1, '3mo', MAX_CENTS])
        self.assertEqual(self.storage.db.execute('SELECT wallet_cents FROM kids').fetchone()[0], MAX_CENTS)
        self.assertEqual(self.storage.db.execute('SELECT count(*) FROM bonds').fetchone()[0], 0)

    def test_invalid_child_action_limits_and_exact_text_retry(self):
        self.svc.mutate('kid', 'add_kid', ['Kid'])
        self.assertTrue(self.svc.mutate('x', 'gift', [1, 3, ' note ']))
        with self.assertRaisesRegex(ValueError, 'different payload'): self.svc.mutate('x', 'gift', [1, 3, 'note'])
        self.assertTrue(self.svc.mutate('x', 'gift', [1, 3, ' note ']))
        for action, args in [([], []), ('transactions', [1, 101]), ('transactions', [99])]:
            with self.assertRaises(ValueError): self.svc.read(action, args)
        for action, args in [('gift', [99, 1]), ('save', [99, 1]), ('invest', [99, '1mo', 1])]:
            with self.assertRaisesRegex(ValueError, 'invalid request'): self.svc.mutate('missing', action, args)

    def test_history_and_ledger_caps_and_persisted_amounts(self):
        self.svc.mutate('kid', 'add_kid', ['Kid'])
        db = self.storage.db
        for table, column in [('transactions', 'amount_cents'), ('history', 'networth_cents'), ('bonds', 'payout_cents')]:
            if table == 'transactions':
                db.execute("INSERT INTO transactions(kid_id,ts,kind,amount_cents) VALUES(1,1,'gift',9007199254740992)")
            elif table == 'history':
                db.execute('INSERT INTO history(kid_id,ts,networth_cents) VALUES(1,1,9007199254740992)')
            else:
                db.execute("INSERT INTO bonds(kid_id,term_key,principal_cents,payout_cents,buy_ts,mature_ts,collected) VALUES(1,'1mo',1,9007199254740992,1,2,0)")
            with self.subTest(table=table), self.assertRaises(ValueError): self.svc.read('kids', [])
            db.execute(f'DELETE FROM {table}')
        db.executemany('INSERT INTO history(kid_id,ts,networth_cents) VALUES(1,1,0)', [()] * 1000)
        self.assertEqual(len(self.svc.read('kids', [])[0]['history']), 1000)
        with self.assertRaises(ValueError): self.svc.mutate('over-history', 'gift', [1, 1])
        self.assertEqual(db.execute('SELECT count(*) FROM history').fetchone()[0], 1000)
        self.assertEqual(db.execute('SELECT wallet_cents FROM kids').fetchone()[0], 500)

    def test_invalid_persisted_rates_and_numbers(self):
        self.svc.mutate('kid', 'add_kid', ['Kid'])
        self.storage.db.execute("INSERT INTO settings VALUES ('savings_weekly_pct', 'NaN')")
        with self.assertRaises(ValueError): self.svc.read('kids', [])
        self.storage.db.execute('DELETE FROM settings')
        self.storage.db.execute('UPDATE kids SET wallet_cents=1.5')
        with self.assertRaises(ValueError): self.svc.read('kids', [])

if __name__ == '__main__': unittest.main()
