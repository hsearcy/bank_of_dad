"""Savings-move interest ledger regressions, using only disposable SQLite."""
import os
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

import bank
import db

NOW = 2_000_000.0
WEEK = 7 * 86400


class SavingsInterestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, 'legacy.db')
        db.init_db(seed=False)
        self.conn = db.get_conn()
        bank.add_kid(self.conn, 'Legacy', '🐣')
        self.kid = self.conn.execute('SELECT id FROM kids').fetchone()['id']
        self.conn.execute('UPDATE kids SET wallet_cents=500, savings_cents=1000, savings_accrued_ts=?', (NOW-WEEK,))
        db.set_setting(self.conn, 'savings_weekly_pct', 10)
        # Existing transactions can predate the fix and must remain untouched.
        bank._tx(self.conn, self.kid, NOW-WEEK, 'save', 1000, 'legacy move')
        self.conn.commit()
        self.clock = patch.object(bank, 'now_ts', return_value=NOW)
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.conn.close()
        db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def state(self):
        return tuple(self.conn.execute('SELECT wallet_cents, savings_cents, savings_accrued_ts FROM kids WHERE id=?', (self.kid,)).fetchone())

    def rows(self):
        return [tuple(r) for r in self.conn.execute('SELECT kind, amount_cents, note, ts FROM transactions WHERE kid_id=? ORDER BY id', (self.kid,))]

    def test_deposit_and_immediate_repeat_and_collect(self):
        self.assertEqual(bank.move_to_savings(self.conn, self.kid, 200), (True, 'Saved!'))
        self.assertEqual(self.state(), (300, 1300, NOW))
        self.assertEqual(self.rows()[1:], [('interest', 100, 'Savings interest', NOW), ('save', 200, 'Moved to savings', NOW)])
        self.assertTrue(bank.move_to_savings(self.conn, self.kid, 100)[0])
        self.assertEqual(bank.collect_rewards(self.conn, self.kid, 'interest'), 0)
        self.assertEqual([r[1] for r in self.rows() if r[0] == 'interest'], [100])
        self.assertEqual(self.state(), (200, 1400, NOW))

    def test_withdraw_caps_to_including_interest(self):
        self.assertEqual(bank.withdraw_savings(self.conn, self.kid, 9999), (True, 'Done!'))
        self.assertEqual(self.state(), (1600, 0, NOW))
        self.assertEqual(self.rows()[1:], [('interest', 100, 'Savings interest', NOW), ('withdraw', 1100, 'Savings to wallet', NOW)])
        self.assertEqual(bank.withdraw_savings(self.conn, self.kid, 1), (False, 'Nothing to take out'))
        self.assertEqual(len(self.rows()), 3)

    def test_deposit_caps_to_wallet_or_rejects_below_minimum(self):
        self.assertTrue(bank.move_to_savings(self.conn, self.kid, 9999)[0])
        self.assertEqual(self.state(), (0, 1600, NOW))
        self.assertEqual(self.rows()[-1][:2], ('save', 500))
        before = (self.state(), self.rows())
        self.assertEqual(bank.move_to_savings(self.conn, self.kid, 100), (False, 'Too little to save'))
        self.assertEqual((self.state(), self.rows()), before)

    def test_rejected_moves_do_not_collect(self):
        for method, amount in [(bank.move_to_savings, 0), (bank.move_to_savings, -1), (bank.withdraw_savings, 0), (bank.withdraw_savings, -1)]:
            before = (self.state(), self.rows())
            self.assertFalse(method(self.conn, self.kid, amount)[0])
            self.assertEqual((self.state(), self.rows()), before)

    def test_rounding_zero_and_negative_interest_preserve_existing_math(self):
        for savings, accrued, expected in [(1, NOW-WEEK, 1), (1000, NOW, 1000), (-1000, NOW-WEEK, -1100)]:
            with self.subTest(savings=savings, accrued=accrued):
                self.conn.execute('UPDATE kids SET wallet_cents=500, savings_cents=?, savings_accrued_ts=?', (savings, accrued))
                self.conn.commit()
                before = len(self.rows())
                self.assertTrue(bank.move_to_savings(self.conn, self.kid, 100)[0])
                self.assertEqual(self.state(), (400, expected+100, NOW))
                self.assertEqual([r[0] for r in self.rows()[before:]], ['save'])

    def test_already_collected_reward_not_duplicated(self):
        self.assertEqual(bank.collect_rewards(self.conn, self.kid, 'interest'), 100)
        self.assertTrue(bank.move_to_savings(self.conn, self.kid, 100)[0])
        self.assertEqual([r[1] for r in self.rows() if r[0] == 'interest'], [100])

    def test_errors_rollback_balance_ledger_and_history(self):
        for action in (bank.move_to_savings, bank.withdraw_savings):
            before = (self.state(), self.rows(), self.conn.execute('SELECT COUNT(*) FROM history').fetchone()[0])
            with patch.object(bank, '_record_history', side_effect=RuntimeError('injected')):
                with self.assertRaisesRegex(RuntimeError, 'injected'):
                    action(self.conn, self.kid, 100)
            self.assertEqual((self.state(), self.rows(), self.conn.execute('SELECT COUNT(*) FROM history').fetchone()[0]), before)
            self.assertFalse(self.conn.in_transaction)

    def test_failed_interest_insert_rolls_back(self):
        self.conn.execute("CREATE TRIGGER fail_interest BEFORE INSERT ON transactions WHEN NEW.kind='interest' BEGIN SELECT RAISE(ABORT, 'injected'); END")
        self.conn.commit()
        for action in (bank.move_to_savings, bank.withdraw_savings):
            before = (self.state(), self.rows())
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'injected'):
                action(self.conn, self.kid, 100)
            self.assertEqual((self.state(), self.rows()), before)

    def test_simultaneous_moves_and_collect_serialize(self):
        barrier = threading.Barrier(3)
        errors = []
        def worker(action):
            conn = db.get_conn()
            try:
                barrier.wait(timeout=5)
                action(conn, self.kid, 100) if action != bank.collect_rewards else action(conn, self.kid, 'interest')
            except Exception as e:
                errors.append(e)
            finally:
                conn.close()
        threads = [threading.Thread(target=worker, args=(action,)) for action in (bank.move_to_savings, bank.withdraw_savings, bank.collect_rewards)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=10)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [])
        self.assertEqual(self.state(), (500, 1100, NOW))
        self.assertEqual([r[1] for r in self.rows() if r[0] == 'interest'], [100])

    def test_api_flow_and_transaction_endpoint(self):
        import app
        app.app.config['TESTING'] = True
        client = app.app.test_client()
        res = client.post('/api/save', json={'kid_id': self.kid, 'amount_cents': 200})
        self.assertEqual(res.get_json(), {'ok': True, 'message': 'Saved!'})
        tx = client.get('/api/transactions', query_string={'kid_id': self.kid}).get_json()['transactions']
        self.assertEqual([(t['kind'], t['amount_cents']) for t in tx[:2]], [('save', 200), ('interest', 100)])
        res = client.post('/api/withdraw', json={'kid_id': self.kid, 'amount_cents': 9999})
        self.assertEqual(res.get_json(), {'ok': True, 'message': 'Done!'})
        self.assertEqual(self.state(), (1600, 0, NOW))


    def test_legacy_schema_is_migrated_without_backfilling(self):
        self.conn.close()
        conn = sqlite3.connect(db.DB_PATH)
        conn.execute('ALTER TABLE history DROP COLUMN wallet_cents')
        conn.execute('ALTER TABLE history DROP COLUMN savings_cents')
        conn.execute('ALTER TABLE history DROP COLUMN invest_cents')
        conn.execute('ALTER TABLE transactions DROP COLUMN bond_id')
        conn.commit()
        conn.close()
        db.init_db(seed=False)
        self.conn = db.get_conn()
        self.assertEqual(self.rows(), [('save', 1000, 'legacy move', NOW-WEEK)])
        self.assertTrue(bank.withdraw_savings(self.conn, self.kid, 200)[0])
        self.assertEqual(self.state(), (700, 900, NOW))
        self.assertEqual([(r[0], r[1]) for r in self.rows()], [('save', 1000), ('interest', 100), ('withdraw', 200)])


if __name__ == '__main__':
    unittest.main()
