import unittest
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
        self.assertTrue(a.mutate(F, self.owner, 'rollback', 'gift', [1, 12]))
        restarted = FamilyAuthorization(self.storage, lambda x: x, lambda: self.now)
        self.assertTrue(restarted.mutate(F, self.owner, 'rollback', 'gift', [1, 12]))

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
