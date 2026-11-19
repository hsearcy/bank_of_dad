"""Disposable SQLite/API safety regression; no live family data."""
import importlib
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import bank
import db
from guest_bank import create_sample


class KidManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {'BANK_OF_DAD_MODE': 'self-hosted'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.path = os.path.join(self.temp.name, 'bank.sqlite')
        self.db_patch = patch.object(db, 'DB_PATH', self.path)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        import app
        self.app = importlib.reload(app).app
        self.app.testing = True
        self.client = self.app.test_client()
        self.conn = db.get_conn()
        self.addCleanup(self.conn.close)
        create_sample(self.conn)
        bank.add_kid(self.conn, 'Sibling')
        self.kid, self.sibling = [r['id'] for r in self.conn.execute('SELECT id FROM kids ORDER BY id')]
        bank.gift(self.conn, self.sibling, 400, 'Keep me')
        bank.move_to_savings(self.conn, self.sibling, 100)
        bank.buy_bond(self.conn, self.sibling, '1mo', 100)
        bank.set_dad_pin(self.conn, '1234')

    def post(self, path, body, pin='1234'):
        return self.client.post('/api/admin/kid/' + path, json=body, headers={'X-Dad-Pin': pin})

    def dump(self):
        return list(self.conn.iterdump())

    def child_rows(self, kid):
        return {t: [tuple(r) for r in self.conn.execute(f'SELECT * FROM {t} WHERE {"id" if t == "kids" else "kid_id"}=? ORDER BY id', (kid,))]
                for t in ('kids', 'bonds', 'transactions', 'history')}

    def test_rename_validates_preserves_ledger_and_handles_missing(self):
        before = self.child_rows(self.kid)
        for name in ('', '   ', 'x' * 81, 123):
            snapshot = self.dump()
            self.assertEqual(400, self.post('update', dict(kid_id=self.kid, name=name)).status_code)
            self.assertEqual(snapshot, self.dump())
        self.assertEqual(200, self.post('update', dict(kid_id=self.kid, name='  New 🐣 name  ')).status_code)
        after = self.child_rows(self.kid)
        self.assertEqual('New 🐣 name', after['kids'][0][1])
        self.assertEqual(before['kids'][0][2:], after['kids'][0][2:])
        for table in ('bonds', 'transactions', 'history'):
            self.assertEqual(before[table], after[table])
        self.assertEqual(404, self.post('update', dict(kid_id=999, name='Missing')).status_code)

    def test_auth_confirmation_scope_and_last_child_not_reseeded(self):
        before = self.dump()
        for path, body in [('update', dict(kid_id=self.kid, name='Nope')),
                           ('remove', dict(kid_id=self.kid, confirm_name='Sample Kid'))]:
            self.assertEqual(403, self.post(path, body, pin='wrong').status_code)
        for body, status in [(dict(kid_id=self.kid), 400), (dict(kid_id=True, confirm_name='Sample Kid'), 400),
                             (dict(kid_id=self.kid, confirm_name='wrong child'), 409)]:
            self.assertEqual(status, self.post('remove', body).status_code)
        self.assertEqual(before, self.dump())
        sibling = self.child_rows(self.sibling)
        self.assertTrue(self.post('remove', dict(kid_id=self.kid, confirm_name='Sample Kid')).json['ok'])
        self.assertTrue(all(not rows for rows in self.child_rows(self.kid).values()))
        self.assertEqual(sibling, self.child_rows(self.sibling))
        self.assertEqual([], list(self.conn.execute('PRAGMA foreign_key_check')))
        self.assertTrue(self.post('remove', dict(kid_id=self.sibling, confirm_name='Sibling')).json['ok'])
        db.init_db(path=self.path)
        self.assertEqual([], self.client.get('/api/state').json['kids'])

    def test_failure_during_cascade_rolls_back_whole_statement(self):
        self.conn.execute("CREATE TRIGGER fail_remove BEFORE DELETE ON history BEGIN SELECT RAISE(ABORT, 'rollback'); END")
        self.conn.commit()
        before = self.dump()
        with self.assertRaises(sqlite3.IntegrityError):
            self.post('remove', dict(kid_id=self.kid, confirm_name='Sample Kid'))
        self.assertEqual(before, self.dump())
        self.assertEqual([], list(self.conn.execute('PRAGMA foreign_key_check')))

    def test_stale_confirmation_after_rename_is_harmless(self):
        self.post('update', dict(kid_id=self.kid, name='Renamed'))
        before = self.dump()
        self.assertEqual(409, self.post('remove', dict(kid_id=self.kid, confirm_name='Sample Kid')).status_code)
        self.assertEqual(before, self.dump())
