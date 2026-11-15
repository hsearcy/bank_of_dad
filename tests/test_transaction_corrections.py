"""Transaction corrections must either apply in full or leave all state alone."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bank
import db


class TransactionCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, 'test.db')
        db.init_db(seed=False)
        self.conn = db.get_conn()
        bank.add_kid(self.conn, 'Test', '🧪')
        self.kid_id = self.conn.execute('SELECT id FROM kids').fetchone()['id']

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def setup_transaction(self, kind, amount, wallet, savings, bond=False):
        self.conn.execute('UPDATE kids SET wallet_cents=?, savings_cents=? WHERE id=?',
                          (wallet, savings, self.kid_id))
        bond_id = None
        if bond:
            bond_id = self.conn.execute(
                "INSERT INTO bonds(kid_id, term_key, principal_cents, payout_cents, "
                "buy_ts, mature_ts, collected) VALUES(?, 'test', 100, 120, 0, 1, 1)",
                (self.kid_id,)).lastrowid
        tx_id = self.conn.execute(
            'INSERT INTO transactions(kid_id, ts, kind, amount_cents, note, bond_id) '
            'VALUES(?, 1, ?, ?, ?, ?)', (self.kid_id, kind, amount, 'original', bond_id)
        ).lastrowid
        self.conn.commit()
        return tx_id

    def snapshot(self):
        return tuple(tuple(tuple(row) for row in self.conn.execute('SELECT * FROM ' + table + ' ORDER BY id'))
                     for table in ('kids', 'transactions', 'bonds', 'history'))

    def test_impossible_deletions_leave_everything_unchanged(self):
        for kind, amount, wallet, savings, bond in [
            ('gift', 500, 100, 0, False),
            ('allowance', 500, 100, 0, False),
            ('interest', 500, 0, 100, False),
            ('save', 500, 0, 100, False),
            ('withdraw', 500, 100, 0, False),
            ('bond_payout', 500, 100, 0, True),
        ]:
            with self.subTest(kind=kind):
                self.conn.execute('DELETE FROM transactions')
                self.conn.execute('DELETE FROM bonds')
                tx_id = self.setup_transaction(kind, amount, wallet, savings, bond)
                before = self.snapshot()
                ok, message = bank.delete_transaction(self.conn, tx_id)
                self.assertFalse(ok, message)
                self.assertTrue(message)
                self.assertEqual(self.snapshot(), before)

    def test_impossible_edits_leave_everything_unchanged(self):
        for kind, amount, new_amount, wallet, savings in [
            ('gift', 500, 100, 100, 0),
            ('allowance', 500, 100, 100, 0),
            ('interest', 500, 100, 0, 100),
            ('spend', -100, 500, 100, 0),
        ]:
            with self.subTest(kind=kind):
                self.conn.execute('DELETE FROM transactions')
                tx_id = self.setup_transaction(kind, amount, wallet, savings)
                before = self.snapshot()
                ok, message = bank.edit_transaction(self.conn, tx_id, new_amount, 'changed')
                self.assertFalse(ok, message)
                self.assertTrue(message)
                self.assertEqual(self.snapshot(), before)

    def test_exact_zero_and_note_only_are_allowed(self):
        tx_id = self.setup_transaction('gift', 500, 100, 0)
        self.assertTrue(bank.edit_transaction(self.conn, tx_id, None, 'note only')[0])
        self.assertTrue(bank.edit_transaction(self.conn, tx_id, 400, 'corrected')[0])
        self.assertEqual(self.conn.execute('SELECT wallet_cents FROM kids').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT amount_cents FROM transactions').fetchone()[0], 400)
        self.conn.execute('UPDATE kids SET wallet_cents=400')
        self.conn.commit()
        self.assertTrue(bank.delete_transaction(self.conn, tx_id)[0])
        self.assertEqual(self.conn.execute('SELECT wallet_cents FROM kids').fetchone()[0], 0)

    def test_valid_transfer_and_payout_deletions_reverse_full_amount(self):
        for kind, wallet, savings, expected, bond in [
            ('save', 50, 500, (550, 0), False),
            ('withdraw', 500, 50, (0, 550), False),
            ('bond_payout', 500, 50, (0, 50), True),
        ]:
            with self.subTest(kind=kind):
                self.conn.execute('DELETE FROM transactions')
                self.conn.execute('DELETE FROM bonds')
                tx_id = self.setup_transaction(kind, 500, wallet, savings, bond)
                self.assertTrue(bank.delete_transaction(self.conn, tx_id)[0])
                row = self.conn.execute('SELECT wallet_cents, savings_cents FROM kids').fetchone()
                self.assertEqual(tuple(row), expected)
                self.assertIsNone(self.conn.execute('SELECT id FROM transactions WHERE id=?',
                                                    (tx_id,)).fetchone())
                if bond:
                    self.assertEqual(self.conn.execute('SELECT collected FROM bonds').fetchone()[0], 0)

    def test_api_reports_rejection(self):
        import app
        app.app.config['TESTING'] = True
        tx_id = self.setup_transaction('gift', 500, 100, 0)
        before = self.snapshot()
        client = app.app.test_client()
        for endpoint, data in [('edit', {'tx_id': tx_id, 'amount_cents': 100, 'note': 'changed'}),
                               ('delete', {'tx_id': tx_id})]:
            with self.subTest(endpoint=endpoint):
                result = client.post('/api/admin/tx/' + endpoint, json=data).get_json()
                self.assertFalse(result['ok'], result)
                self.assertTrue(result['message'])
                self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
