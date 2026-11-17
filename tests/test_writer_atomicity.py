"""Disposable, deterministic SQLite writer and transaction ownership regressions."""
import os
from contextlib import closing
import sqlite3
import subprocess
import sys
import select
import tempfile
import threading
import unittest
from unittest.mock import patch

import bank
import config
import db


class WriterAtomicityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'test.db')
        self.conn = self.connect()
        self.conn.executescript(db.SCHEMA)
        bank.add_kid(self.conn, 'test')
        self.kid = self.conn.execute('SELECT id FROM kids').fetchone()[0]
        self.conn.execute('UPDATE kids SET wallet_cents=1000, savings_cents=0 WHERE id=?', (self.kid,))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def connect(self, **kwargs):
        c = sqlite3.connect(self.path, timeout=3, **kwargs)
        c.row_factory = sqlite3.Row
        return c

    def state(self):
        return tuple(self.conn.execute('SELECT wallet_cents, savings_cents FROM kids').fetchone())

    def test_nested_failure_and_outer_commit_or_rollback(self):
        for finish in ('commit', 'rollback'):
            with self.subTest(finish=finish):
                self.conn.execute('BEGIN')
                db.set_setting(self.conn, 'unrelated', finish)
                before = self.state()
                with patch.object(bank, '_record_history', side_effect=RuntimeError('injected')):
                    with self.assertRaisesRegex(RuntimeError, 'injected'):
                        bank.move_to_savings(self.conn, self.kid, 100)
                self.assertTrue(self.conn.in_transaction)
                self.assertEqual(self.state(), before)
                self.assertEqual(db.get_setting(self.conn, 'unrelated'), finish)
                self.assertTrue(bank.gift(self.conn, self.kid, 100))
                self.assertTrue(self.conn.in_transaction)
                with closing(self.connect()) as observer:
                    self.assertEqual(observer.execute('SELECT wallet_cents FROM kids').fetchone()[0], before[0])
                getattr(self.conn, finish)()
                self.assertFalse(self.conn.in_transaction)
                self.assertEqual(self.state()[0], before[0] + (100 if finish == 'commit' else 0))
                self.assertEqual(db.get_setting(self.conn, 'unrelated'), 'commit')

    def test_autocommit_connection_app_owned_failure_and_success(self):
        with closing(self.connect(isolation_level=None)) as conn:
            before = self.state()
            with patch.object(bank, '_record_history', side_effect=RuntimeError('injected')):
                with self.assertRaisesRegex(RuntimeError, 'injected'):
                    bank.gift(conn, self.kid, 100)
            self.assertFalse(conn.in_transaction)
            self.assertEqual(self.state(), before)
            self.assertEqual(conn.execute('SELECT count(*) FROM transactions').fetchone()[0], 0)
            self.assertTrue(bank.gift(conn, self.kid, 100))
            self.assertFalse(conn.in_transaction)
            self.assertEqual(self.state()[0], before[0] + 100)

    def test_save_serializes_with_other_balance_writers(self):
        for other in ('gift', 'spend', 'bond', 'edit', 'delete'):
            with self.subTest(other=other):
                self.conn.execute('DELETE FROM transactions')
                self.conn.execute('DELETE FROM bonds')
                self.conn.execute('DELETE FROM history')
                self.conn.execute('UPDATE kids SET wallet_cents=1000, savings_cents=0')
                self.conn.commit()
                self.assertTrue(bank.gift(self.conn, self.kid, 100))
                tx_id = self.conn.execute("SELECT id FROM transactions WHERE kind='gift'").fetchone()[0]
                entered, release, attempting, read = (threading.Event() for _ in range(4))
                errors = []

                class Instrumented(sqlite3.Connection):
                    role = ''
                    def execute(inner, sql, parameters=()):
                        if inner.role == 'other' and sql == 'BEGIN IMMEDIATE':
                            attempting.set()
                        result = super().execute(sql, parameters)
                        if sql.startswith('SELECT * FROM kids WHERE id='):
                            if inner.role == 'save':
                                entered.set()
                                if not release.wait(3):
                                    raise RuntimeError('save wait timed out')
                            else:
                                read.set()
                        return result

                def worker(role):
                    conn = self.connect(factory=Instrumented)
                    conn.role = role
                    try:
                        if role == 'save':
                            bank.move_to_savings(conn, self.kid, 200)
                        elif other == 'gift':
                            bank.gift(conn, self.kid, 100)
                        elif other == 'spend':
                            bank.spend(conn, self.kid, 100)
                        elif other == 'bond':
                            bank.buy_bond(conn, self.kid, next(iter(config.BONDS)), 100)
                        elif other == 'edit':
                            bank.edit_transaction(conn, tx_id, 200, 'edited')
                        else:
                            bank.delete_transaction(conn, tx_id)
                    except BaseException as exc:
                        errors.append(exc)
                    finally:
                        conn.close()

                a = threading.Thread(target=worker, args=('save',))
                b = threading.Thread(target=worker, args=('other',))
                a.start()
                try:
                    self.assertTrue(entered.wait(3))
                    b.start()
                    self.assertTrue(attempting.wait(3), 'other writer did not acquire before reading')
                    self.assertFalse(read.wait(.15), 'other writer read while save held writer lock')
                finally:
                    release.set()
                    a.join(5)
                    if b.ident: b.join(5)
                self.assertFalse(a.is_alive() or b.is_alive())
                self.assertEqual(errors, [])
                expected_wallet = {'gift': 1000, 'spend': 800, 'bond': 800, 'edit': 1000, 'delete': 800}[other]
                self.assertEqual(self.state(), (expected_wallet, 200))
                history = self.conn.execute('SELECT wallet_cents, savings_cents, invest_cents FROM history ORDER BY id DESC LIMIT 1').fetchone()
                invested = 100 if other == 'bond' else 0
                self.assertEqual(tuple(history), (expected_wallet, 200, invested))
                self.assertEqual(self.conn.execute("SELECT count(*) FROM transactions WHERE kind='save'").fetchone()[0], 1)

    def test_writer_lock_crosses_process_boundary(self):
        self.conn.execute('BEGIN IMMEDIATE')
        code = '''
import sqlite3, sys, bank
c = sqlite3.connect(sys.argv[1], timeout=3)
c.row_factory = sqlite3.Row
print("ready", flush=True)
assert bank.gift(c, int(sys.argv[2]), 100)
print("done", flush=True)
c.close()
'''
        proc = subprocess.Popen([sys.executable, '-u', '-c', code, self.path, str(self.kid)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(), 'ready')
            self.assertFalse(select.select([proc.stdout], [], [], .2)[0])
            self.assertTrue(bank.move_to_savings(self.conn, self.kid, 200)[0])
            self.conn.commit()
            stdout, stderr = proc.communicate(timeout=5)
            self.assertEqual(proc.returncode, 0, stderr)
            self.assertEqual(stdout.strip(), 'done')
            self.assertEqual(self.state(), (900, 200))
        finally:
            if self.conn.in_transaction:
                self.conn.rollback()
            if proc.poll() is None:
                proc.kill()
                proc.communicate()

    def test_unknown_adapter_fails_before_any_sql(self):
        class Unsupported:
            in_transaction = False
            def execute(self, *args):
                raise AssertionError('adapter SQL must not execute')
        with self.assertRaisesRegex(TypeError, 'sqlite3.Connection'):
            bank.gift(Unsupported(), self.kid, 100)

    def test_wal_stale_caller_snapshot_fails_without_destroying_outer_transaction(self):
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('BEGIN')
        self.state()  # establish read snapshot
        with closing(self.connect()) as writer:
            self.assertTrue(bank.gift(writer, self.kid, 100))
        with self.assertRaises(sqlite3.OperationalError):
            bank.move_to_savings(self.conn, self.kid, 100)
        self.assertTrue(self.conn.in_transaction)
        self.assertEqual(self.state(), (1000, 0))  # original snapshot intact
        self.conn.rollback()
        self.assertEqual(self.state(), (1100, 0))


if __name__ == '__main__':
    unittest.main()
