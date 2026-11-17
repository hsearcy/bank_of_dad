"""Synthetic capability and rollback regressions; no real family data."""
import unittest

import bank
from hosted.adapter import Connection
from hosted.service import FamilyService
from test_foundation import Storage


class CapabilityTest(unittest.TestCase):
    def setUp(self):
        self.storage = Storage()
        self.addCleanup(self.storage.db.close)
        self.service = FamilyService(self.storage, lambda cb: cb)
        self.service.mutate('seed', 'add_kid', ['Kid'])

    def test_absent_expired_wrong_connection_and_duck_type(self):
        c = self.service.conn
        with self.assertRaises(RuntimeError): bank.gift(c, 1, 1)
        with self.assertRaises(RuntimeError): self.service._transaction(lambda x: bank.gift(x, 1, 1))
        def use_expired(x):
            with x._authorized_bank():
                saved = x._capability
            x._capability = saved
            # Reattaching an expired capability must not revive it.
            with self.assertRaises(RuntimeError): bank.gift(x, 1, 1)
        with self.assertRaisesRegex(RuntimeError, 'poisoned'):
            self.service._transaction(use_expired)
        other = Connection(self.storage.sql)
        def wrong(x):
            with x._authorized_bank():
                other._active = True
                other._capability = x._capability
                try:
                    with self.assertRaises(RuntimeError): bank.gift(other, 1, 1)
                finally:
                    other._active = False
                    other._capability = None
        with self.assertRaisesRegex(RuntimeError, 'poisoned'):
            self.service._transaction(wrong)
        class Pretender: pass
        with self.assertRaises(TypeError): bank.gift(Pretender(), 1, 1)
        self.assertEqual(self.service.read('transactions', [1]), [])

    def test_swallowed_reentry_and_sql_errors_poison_transaction(self):
        def reentrant(c):
            with c._authorized_bank():
                def action(conn):
                    conn.execute("UPDATE kids SET wallet_cents=999 WHERE id=1")
                    try: bank.gift(conn, 1, 1)
                    except RuntimeError: pass
                c._bank_action(action, (), {})
        with self.assertRaisesRegex(RuntimeError, 'poisoned'):
            self.service._transaction(reentrant)
        def bad_sql(c):
            c.execute('UPDATE kids SET wallet_cents=999 WHERE id=1')
            try: c.execute('SELECT * FROM nonexistent_table')
            except Exception: pass
        with self.assertRaisesRegex(RuntimeError, 'poisoned'):
            self.service._transaction(bad_sql)
        self.assertEqual(self.service.read('kids', [])[0]['wallet_cents'], 500)
        self.assertIsNone(self.service.conn._capability)
        self.assertFalse(self.service.conn._active)
        self.assertTrue(self.service.mutate('after', 'gift', [1, 1]))

    def test_swallowed_nested_transaction_poison(self):
        def op(c):
            c.execute('UPDATE kids SET wallet_cents=999 WHERE id=1')
            try: self.service.initialize()
            except RuntimeError: pass
        with self.assertRaisesRegex(RuntimeError, 'poisoned'):
            self.service._transaction(op)
        self.assertEqual(self.service.read('kids', [])[0]['wallet_cents'], 500)

    def test_caught_application_errors_poison_but_business_failure_commits(self):
        for callback in (
            lambda c: self.service._mutate(c, '', 'gift', [1, 1]),
            lambda c: self.service._mutate(c, 'bad', 'gift', [1, -1]),
            lambda c: self.service._read(c, 'transactions', [-1]),
        ):
            def op(c):
                c.execute('UPDATE kids SET wallet_cents=999 WHERE id=1')
                try:
                    callback(c)
                except ValueError:
                    pass
            with self.assertRaisesRegex(RuntimeError, 'poisoned'):
                self.service._transaction(op)
            self.assertEqual(self.service.read('kids', [])[0]['wallet_cents'], 500)
        self.assertEqual(self.service.mutate('empty', 'spend', [1, 500]), [True, 'Spent!'])
        self.assertEqual(self.service.mutate('business-failure', 'spend', [1, 1]),
                         [False, 'No coins to spend'])
        self.assertEqual(self.service.mutate('business-failure', 'spend', [1, 1]),
                         [False, 'No coins to spend'])
