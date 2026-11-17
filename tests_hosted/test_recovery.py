import concurrent.futures
import sqlite3
import threading
import unittest
from unittest.mock import patch

from test_foundation import Storage
from test_family_auth import F, OWNER, PARENT, snapshot
from test_recovery_matrix import full_snapshot
from hosted.family_auth import FamilyAuthorization
from hosted.recovery import RecoveryAuthorization


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.s = Storage()
        self.addCleanup(lambda: self.s.db.close())
        self.a = RecoveryAuthorization(self.s, lambda f: f, lambda: 1000)
        self.a.bootstrap_privileged(F, OWNER)
        self.o = self.a.issue_grant_privileged(F, OWNER, 100)
        self.a.set_member(F, self.o, PARENT, 'parent', 'active')
        self.p = self.a.issue_grant_privileged(F, PARENT, 100)

    def ack(self, token, op, result):
        return self.a.acknowledge(F, token, op, result)

    def seed(self):
        self.assertTrue(self.a.mutate(F, self.o, 'seed', 'add_kid', ['Synthetic']))
        self.ack(self.o, 'seed', True)

    def test_lost_response_rotation_and_principal_isolation(self):
        self.seed()
        self.assertTrue(self.a.mutate(F, self.p, 'gift', 'gift', [1, 11]))
        # Treat the result as lost; all subsequent state is discovered server-side.
        replacement = self.a.issue_grant_privileged(F, PARENT, 100)
        self.a.revoke(F, self.p)
        restarted = RecoveryAuthorization(self.s, lambda f: f, lambda: 1000)
        outcome = restarted.pending(F, replacement)
        self.assertEqual(outcome, {'operation_id': 'gift', 'request': ['gift', [1, 11, 'From Dad']], 'result': True, 'acknowledged': False})
        self.assertIsNone(restarted.pending(F, self.o))
        self.assertIsNone(restarted.outcome(F, self.o, 'gift'))
        for f in (lambda: restarted.pending(F, self.p), lambda: restarted.outcome(F, self.p, 'gift'),
                  lambda: restarted.acknowledge(F, self.p, 'gift', True),
                  lambda: restarted.mutate(F, self.p, 'gift', 'gift', [1, 11])):
            with self.assertRaises(PermissionError): f()
        with self.assertRaises(PermissionError): restarted.mutate(F, self.o, 'gift', 'gift', [1, 11])
        with self.assertRaises(ValueError): restarted.mutate(F, replacement, 'gift', 'gift', [1, 12])
        with self.assertRaisesRegex(ValueError, 'unacknowledged'): restarted.mutate(F, replacement, 'new', 'gift', [1, 11])
        with self.assertRaises(ValueError): restarted.acknowledge(F, replacement, 'gift', 1)
        with self.assertRaises(ValueError): restarted.acknowledge(F, replacement, 'unknown', True)
        self.assertTrue(restarted.mutate(F, replacement, 'gift', 'gift', [1, 11, 'From Dad']))
        self.assertTrue(restarted.acknowledge(F, replacement, 'gift', True))
        self.assertTrue(restarted.acknowledge(F, replacement, 'gift', True))
        self.assertTrue(restarted.outcome(F, replacement, 'gift')['acknowledged'])
        self.assertIsNone(restarted.pending(F, replacement))
        self.assertTrue(restarted.mutate(F, replacement, 'new', 'gift', [1, 7]))
        self.assertEqual(self.a.read(F, self.o, 'kids', [])[0]['wallet_cents'], 518)

    def test_failure_is_reviewable_without_bank_change(self):
        self.seed()
        spent = self.a.mutate(F, self.o, 'empty', 'spend', [1, 500])
        self.ack(self.o, 'empty', spent)
        result = self.a.mutate(F, self.p, 'failure', 'spend', [1, 1])
        self.assertEqual(result, [False, 'No coins to spend'])
        self.assertEqual(self.a.read(F, self.o, 'kids', [])[0]['wallet_cents'], 0)
        self.assertEqual(self.a.pending(F, self.p)['result'], result)
        self.assertEqual(self.a.mutate(F, self.p, 'failure', 'spend', [1, 1]), result)
        self.ack(self.p, 'failure', result)

    def test_faults_rollback_and_schema_upgrade_refused(self):
        self.seed()
        before = full_snapshot(self.s)
        for prefix in ('UPDATE kids SET wallet_cents', 'INSERT INTO hosted_operations', 'INSERT INTO local_operations', 'INSERT INTO local_recovery'):
            for after in (False, True):
                original = self.s.exec
                hit = []
                def fault(sql, *args):
                    if sql.startswith(prefix):
                        hit.append(True)
                        if not after: raise RuntimeError('injected')
                    value = original(sql, *args)
                    if sql.startswith(prefix) and after: raise RuntimeError('injected')
                    return value
                with self.subTest(prefix=prefix, after=after), patch.object(self.s, 'exec', side_effect=fault):
                    with self.assertRaisesRegex(RuntimeError, 'injected'): self.a.mutate(F, self.o, 'fault', 'gift', [1, 9])
                self.assertTrue(hit)
                self.assertEqual(full_snapshot(self.s), before)
        with self.assertRaises(RuntimeError):
            FamilyAuthorization(self.s, lambda f: f, lambda: 1000).read(F, self.o, 'kids', [])
        old = Storage(); self.addCleanup(old.db.close)
        legacy = FamilyAuthorization(old, lambda f: f, lambda: 1000)
        legacy.bootstrap_privileged(F, OWNER)
        before_schema = list(old.db.execute('SELECT sql FROM sqlite_master ORDER BY name'))
        with self.assertRaises(RuntimeError): RecoveryAuthorization(old, lambda f: f).bootstrap_privileged(F, OWNER)
        self.assertEqual(list(old.db.execute('SELECT sql FROM sqlite_master ORDER BY name')), before_schema)
        with self.assertRaises(RuntimeError): RecoveryAuthorization(old, lambda f: f).pending(F, 'fake')

    def test_database_restore_and_membership_denial(self):
        self.seed()
        self.a.mutate(F, self.p, 'lost', 'gift', [1, 4])
        restored = Storage(); self.addCleanup(restored.db.close)
        self.s.db.backup(restored.db)
        a = RecoveryAuthorization(restored, lambda f: f, lambda: 1000)
        self.assertEqual(a.pending(F, self.p)['operation_id'], 'lost')
        # Existing adapter's INSERT-ON-CONFLICT guard needs a prior insert on a
        # freshly restored connection; exercise restore without hiding that limit.
        a.issue_grant_privileged(F, OWNER, 100)
        a.set_member(F, self.o, PARENT, 'parent', 'disabled')
        for fn in (lambda: a.pending(F, self.p), lambda: a.outcome(F, self.p, 'lost'),
                   lambda: a.acknowledge(F, self.p, 'lost', True), lambda: a.mutate(F, self.p, 'lost', 'gift', [1, 4])):
            with self.assertRaises(PermissionError): fn()
        a.set_member(F, self.o, PARENT, 'parent', 'active')
        fresh = a.issue_grant_privileged(F, PARENT, 100)
        self.assertEqual(a.pending(F, fresh)['operation_id'], 'lost')

    def test_ack_fault_rolls_back_and_wrong_family_denied(self):
        self.seed()
        self.a.mutate(F, self.p, 'pending', 'gift', [1, 8])
        before = full_snapshot(self.s)
        original = self.s.exec
        def fault(sql, *args):
            result = original(sql, *args)
            if sql.startswith('UPDATE local_recovery SET acknowledged=1'):
                raise RuntimeError('ack fault')
            return result
        with patch.object(self.s, 'exec', side_effect=fault):
            with self.assertRaisesRegex(RuntimeError, 'ack fault'):
                self.ack(self.p, 'pending', True)
        self.assertEqual(full_snapshot(self.s), before)
        self.assertFalse(self.a.pending(F, self.p)['acknowledged'])
        with self.assertRaises(ValueError):
            self.a.mutate(F, self.p, 'blocked', 'gift', [1, 1])
        with self.assertRaises(PermissionError):
            self.a.pending('b'*32, self.p)
        self.assertTrue(self.ack(self.p, 'pending', True))

    def test_competing_operations_and_ack(self):
        self.seed()
        self.s.db.close()
        # Retain database contents when recreating storage for a threaded test via backup is
        # unnecessary here: seed a fresh serialized SQLite storage instead.
        self.s.db = sqlite3.connect(':memory:', isolation_level=None, check_same_thread=False)
        self.s.db.row_factory = sqlite3.Row
        lock = threading.Lock()
        tx = self.s.transactionSync
        self.s.transactionSync = lambda callback: self._locked(lock, tx, callback)
        setup = RecoveryAuthorization(self.s, lambda f: f, lambda: 1000)
        setup.bootstrap_privileged(F, OWNER)
        token = setup.issue_grant_privileged(F, OWNER, 100)
        setup.mutate(F, token, 'seed', 'add_kid', ['Synthetic'])
        setup.acknowledge(F, token, 'seed', True)
        barrier = threading.Barrier(2)
        def run(i):
            a = RecoveryAuthorization(self.s, lambda f: f, lambda: 1000)
            barrier.wait()
            try: return a.mutate(F, token, 'race'+str(i), 'gift', [1, 2])
            except ValueError as e: return str(e)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, range(2)))
        self.assertCountEqual(results, [True, 'unacknowledged operation'])
        pending = setup.pending(F, token)
        barrier = threading.Barrier(2)
        def acknowledge(_):
            a = RecoveryAuthorization(self.s, lambda f: f, lambda: 1000)
            barrier.wait()
            return a.acknowledge(F, token, pending['operation_id'], True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(acknowledge, range(2))), [True, True])
        self.assertEqual(setup.read(F, token, 'kids', [])[0]['wallet_cents'], 502)

    @staticmethod
    def _locked(lock, tx, callback):
        with lock: return tx(callback)

if __name__ == '__main__': unittest.main()
