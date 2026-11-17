import unittest
import sqlite3
import threading
import concurrent.futures
from unittest.mock import patch
from test_foundation import Storage
from hosted.family_auth import FamilyAuthorization, MAX_TTL

F = 'a' * 32
OWNER = {'issuer': 'https://issuer.test', 'subject': 'owner'}
PARENT = {'issuer': 'https://issuer.test', 'subject': 'parent'}
OTHER = {'issuer': 'https://issuer.test', 'subject': 'other'}

class AuthorizationTest(unittest.TestCase):
    def setUp(self):
        self.storage = Storage(); self.addCleanup(self.storage.db.close)
        self.now = 1000
        self.a = FamilyAuthorization(self.storage, lambda x: x, lambda: self.now)
        self.a.bootstrap_privileged(F, OWNER)
        self.owner = self.a.issue_grant_privileged(F, OWNER, 100)
        self.a.set_member(F, self.owner, PARENT, 'parent', 'active')
        self.parent = self.a.issue_grant_privileged(F, PARENT, 100)

    def test_policy_revocation_expiry_and_ordering(self):
        a = self.a
        with self.assertRaises(PermissionError): a.set_member(F, self.parent, OTHER, 'parent', 'active')
        with self.assertRaises(PermissionError): a.issue_grant_privileged(F, OTHER, 10)
        with self.assertRaises(PermissionError): a.read('b'*32, self.owner, 'kids', [])
        with self.assertRaises(ValueError): a.issue_grant_privileged(F, OWNER, MAX_TTL + 1)
        a.mutate(F, self.parent, 'kid', 'add_kid', ['Synthetic'])
        a.revoke(F, self.parent)
        with self.assertRaises(PermissionError): a.mutate(F, self.parent, 'kid', 'add_kid', ['Synthetic'])
        self.assertEqual(len(a.read(F, self.owner, 'kids', [])), 1)
        p2 = a.issue_grant_privileged(F, PARENT, 2)
        a.set_member(F, self.owner, PARENT, 'parent', 'disabled')
        with self.assertRaises(PermissionError): a.mutate(F, p2, 'denied', 'gift', [1, 5])
        a.set_member(F, self.owner, PARENT, 'parent', 'active')
        with self.assertRaises(PermissionError): a.read(F, p2, 'kids', [])
        p3 = a.issue_grant_privileged(F, PARENT, 2)
        self.now += 2
        with self.assertRaises(PermissionError): a.read(F, p3, 'kids', [])

    def test_idempotency_actor_and_rollback(self):
        a = self.a
        a.mutate(F, self.owner, 'kid', 'add_kid', ['Synthetic'])
        a.mutate(F, self.owner, 'gift', 'gift', [1, 10])
        with self.assertRaises(PermissionError): a.mutate(F, self.parent, 'gift', 'gift', [1, 10])
        with self.assertRaises(ValueError): a.mutate(F, self.owner, 'gift', 'gift', [1, 11])
        before = a.read(F, self.owner, 'kids', [])
        original = self.storage.exec
        def fault(sql, *args):
            r = original(sql, *args)
            if sql.startswith('INSERT INTO local_operations'): raise RuntimeError('post-bank fault')
            return r
        with patch.object(self.storage, 'exec', side_effect=fault):
            with self.assertRaisesRegex(RuntimeError, 'post-bank fault'): a.mutate(F, self.owner, 'rollback', 'gift', [1, 12])
        self.assertEqual(before, a.read(F, self.owner, 'kids', []))
        self.assertIsNone(self.storage.db.execute("SELECT * FROM hosted_operations WHERE operation_id='rollback'").fetchone())
        self.assertIsNone(self.storage.db.execute("SELECT * FROM local_operations WHERE operation_id='rollback'").fetchone())
        self.assertTrue(a.mutate(F, self.owner, 'rollback', 'gift', [1, 12]))
        restarted = FamilyAuthorization(self.storage, lambda x: x, lambda: self.now)
        self.assertTrue(restarted.mutate(F, self.owner, 'rollback', 'gift', [1, 12]))

    def test_action_role_matrix_and_same_principal_new_grant_retry(self):
        from hosted.service import _MUTATIONS, _READS
        cases = {'add_kid': ['Extra'], 'gift': [1, 100], 'save': [1, 10],
                 'withdraw': [1, 1], 'spend': [1, 1], 'invest': [1, '1mo', 1],
                 'collect_rewards': [1]}
        reads = {'kids': [], 'transactions': [1]}
        self.assertEqual(set(cases), set(_MUTATIONS))
        self.assertEqual(set(reads), set(_READS))
        self.a.mutate(F, self.owner, 'seed', 'add_kid', ['Seed'])
        disabled = {'issuer': 'https://issuer.test', 'subject': 'disabled'}
        self.a.set_member(F, self.owner, disabled, 'parent', 'active')
        disabled_token = self.a.issue_grant_privileged(F, disabled, 100)
        self.a.set_member(F, self.owner, disabled, 'parent', 'disabled')
        for role, token, allowed in [('owner', self.owner, True), ('parent', self.parent, True),
                                     ('disabled', disabled_token, False), ('invalid', 'invalid', False)]:
            for action, args in cases.items():
                with self.subTest(role=role, action=action):
                    if allowed:
                        self.a.mutate(F, token, role + action, action, args)
                    else:
                        with self.assertRaises(PermissionError):
                            self.a.mutate(F, token, role + action, action, args)
            for action, args in reads.items():
                with self.subTest(role=role, action=action):
                    if allowed: self.a.read(F, token, action, args)
                    else:
                        with self.assertRaises(PermissionError): self.a.read(F, token, action, args)
        result = self.a.mutate(F, self.parent, 'same-principal', 'gift', [1, 7])
        replacement = self.a.issue_grant_privileged(F, PARENT, 100)
        self.a.revoke(F, self.parent)
        before = self.a.read(F, self.owner, 'kids', [])
        self.assertEqual(self.a.mutate(F, replacement, 'same-principal', 'gift', [1, 7]), result)
        self.assertEqual(self.a.read(F, self.owner, 'kids', []), before)

    def test_schema_and_marker_variants_fail_closed(self):
        alterations = [
            "DROP TABLE local_operations",
            "CREATE INDEX local_extra ON local_members(role)",
            "CREATE TRIGGER local_extra AFTER INSERT ON local_members BEGIN SELECT 1; END",
            "ALTER TABLE local_members ADD COLUMN extra TEXT",
            "DROP TABLE local_grants; CREATE TABLE local_grants (digest TEXT PRIMARY KEY, issuer TEXT NOT NULL, subject TEXT NOT NULL, expires INTEGER NOT NULL, revoked INTEGER NOT NULL)",
        ]
        for sql in alterations:
            with self.subTest(sql=sql):
                s = Storage(); self.addCleanup(s.db.close)
                a = FamilyAuthorization(s, lambda x: x, lambda: self.now)
                a.bootstrap_privileged(F, OWNER); token = a.issue_grant_privileged(F, OWNER, 100)
                s.db.executescript(sql)
                with self.assertRaisesRegex(RuntimeError, 'schema'): a.read(F, token, 'kids', [])
        for sql in ['DELETE FROM local_auth_schema',
                    'INSERT INTO local_auth_schema VALUES (1, "' + F + '")',
                    'UPDATE local_auth_schema SET version=2',
                    'UPDATE local_auth_schema SET version="bad"',
                    'UPDATE local_auth_schema SET family="' + 'b'*32 + '"']:
            with self.subTest(sql=sql):
                s = Storage(); self.addCleanup(s.db.close)
                a = FamilyAuthorization(s, lambda x: x, lambda: self.now)
                a.bootstrap_privileged(F, OWNER); token = a.issue_grant_privileged(F, OWNER, 100)
                s.db.execute(sql)
                with self.assertRaises(PermissionError): a.read(F, token, 'kids', [])

    def test_controlled_transaction_ordering(self):
        # This lock models transactionSync serialization; the paused first callback
        # exposes a genuine overlapping attempt, not a sequential ordering claim.
        for first in ('operation', 'revoke'):
            with self.subTest(first=first):
                s = Storage(); s.db.close()
                s.db = sqlite3.connect(':memory:', isolation_level=None, check_same_thread=False)
                s.db.row_factory = sqlite3.Row
                self.addCleanup(s.db.close)
                lock = threading.Lock(); entered = threading.Event(); release = threading.Event(); attempted = threading.Event(); acquired = threading.Event()
                thread_state = threading.local()
                original_tx = s.transactionSync
                def serial(callback):
                    contender = getattr(thread_state, 'contender', False)
                    if contender: attempted.set()
                    with lock:
                        if contender: acquired.set()
                        return original_tx(callback)
                s.transactionSync = serial
                setup = FamilyAuthorization(s, lambda x: x, lambda: self.now)
                setup.bootstrap_privileged(F, OWNER); token = setup.issue_grant_privileged(F, OWNER, 100)
                setup.mutate(F, token, 'seed', 'add_kid', ['Seed'])
                a = FamilyAuthorization(s, lambda x: x, lambda: self.now)
                b = FamilyAuthorization(s, lambda x: x, lambda: self.now)
                original_auth = a._auth
                def pause(*args, **kwargs):
                    result = original_auth(*args, **kwargs)
                    entered.set()
                    if not release.wait(10): raise RuntimeError('schedule timed out')
                    return result
                a._auth = pause
                def operation(obj): return obj.mutate(F, token, 'race', 'gift', [1, 13])
                def revoke(obj): return obj.revoke(F, token)
                def second():
                    thread_state.contender = True
                    return (revoke if first == 'operation' else operation)(b)
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    f1 = pool.submit(operation if first == 'operation' else revoke, a)
                    try:
                        self.assertTrue(entered.wait(10))
                        f2 = pool.submit(second); self.assertTrue(attempted.wait(10))
                        with self.assertRaises(concurrent.futures.TimeoutError): f2.result(timeout=.1)
                        self.assertFalse(acquired.is_set())
                    finally: release.set()
                    f1.result(timeout=10)
                    if first == 'operation': f2.result(timeout=10)
                    else:
                        with self.assertRaises(PermissionError): f2.result(timeout=10)
                self.assertTrue(acquired.is_set())
                count = s.db.execute("SELECT count(*) FROM hosted_operations WHERE operation_id='race'").fetchone()[0]
                self.assertEqual(count, int(first == 'operation'))
                self.assertEqual(s.db.execute("SELECT count(*) FROM local_operations WHERE operation_id='race'").fetchone()[0], count)

    def test_capacity_and_nested_guard(self):
        with patch('hosted.family_auth.MAX_GRANTS', 2):
            with self.assertRaisesRegex(ValueError, 'capacity'): self.a.issue_grant_privileged(F, OWNER, 10)
            self.a.revoke(F, self.parent)
            self.a.issue_grant_privileged(F, OWNER, 10)
        with self.assertRaisesRegex(RuntimeError, 'nested transaction'):
            self.a.bank._transaction(lambda c: self.a.read(F, self.owner, 'kids', []))

    def test_bootstrap_failure_and_nonempty_rejected(self):
        s = Storage(); self.addCleanup(s.db.close)
        a = FamilyAuthorization(s, lambda x: x, lambda: self.now)
        original = s.exec
        def fault(sql, *args):
            result = original(sql, *args)
            if sql.startswith('INSERT INTO local_members'): raise RuntimeError('bootstrap fault')
            return result
        with patch.object(s, 'exec', side_effect=fault):
            with self.assertRaisesRegex(RuntimeError, 'bootstrap fault'): a.bootstrap_privileged(F, OWNER)
        self.assertEqual(s.db.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall(), [])
        s.db.execute('CREATE VIEW prior AS SELECT 1')
        with self.assertRaisesRegex(RuntimeError, 'empty database'): a.bootstrap_privileged(F, OWNER)

    def test_fail_closed_schema_and_bootstrap(self):
        with self.assertRaises(RuntimeError): self.a.bootstrap_privileged(F, OWNER)
        self.storage.db.execute('DROP TABLE local_members')
        with self.assertRaises(RuntimeError): self.a.read(F, self.owner, 'kids', [])

if __name__ == '__main__': unittest.main()
