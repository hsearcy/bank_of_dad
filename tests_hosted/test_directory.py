import unittest
from unittest.mock import patch
from test_foundation import Storage
from hosted.directory import Directory

P = {'issuer': 'https://id.example', 'subject': 'alice'}
Q = {'issuer': 'https://id.example', 'subject': 'bob'}

class DirectoryTest(unittest.TestCase):
    def setUp(self):
        self.storage = Storage()
        self.addCleanup(self.storage.db.close)
        self.d = Directory(self.storage, proxy_factory=lambda cb: cb, clock=lambda: 1000)
    def test_membership_sessions_invites(self):
        family, session = self.d.create_family(P)
        self.assertEqual(self.d.authorize(session, family)['role'], 'owner')
        family2, other = self.d.create_family(Q)
        with self.assertRaises(PermissionError): self.d.authorize(other, family)
        invitation = self.d.invite(session, family, 'parent', 10)
        bob = self.d.accept_invite(Q, invitation)
        self.assertEqual(self.d.authorize(bob, family)['role'], 'parent')
        self.assertEqual(self.d.my_memberships(bob), [{'family': family, 'role': 'parent', 'status': 'active'}, {'family': family2, 'role': 'owner', 'status': 'active'}] if family < family2 else [{'family': family2, 'role': 'owner', 'status': 'active'}, {'family': family, 'role': 'parent', 'status': 'active'}])
        with self.assertRaises(PermissionError): self.d.accept_invite(Q, invitation)
        with self.assertRaises(PermissionError): self.d.invite(bob, family, 'parent', 10)
        self.d.revoke(bob)
        with self.assertRaises(PermissionError): self.d.authorize(bob, family)
        self.assertEqual(len(self.d.members(session, family)), 2)
        self.assertNotEqual(family, family2)
    def test_bad_and_expired(self):
        for bad in ({}, {'issuer':'http://bad', 'subject':'x'}, {'issuer':'https://id.example', 'subject':''}, None):
            with self.assertRaises(ValueError): self.d.create_family(bad)
        family, session = self.d.create_family(P)
        for ttl in (0, -1, True, 999999999):
            with self.assertRaises(ValueError): self.d.invite(session, family, 'parent', ttl)
        token = self.d.invite(session, family, 'parent', 1)
        self.d.clock = lambda: 1001
        with self.assertRaises(PermissionError): self.d.accept_invite(Q, token)
        with self.assertRaises(PermissionError): self.d.authorize('bad', family)
    def test_invite_redemption_rollback(self):
        family, session = self.d.create_family(P)
        token = self.d.invite(session, family, 'parent', 10)
        original = self.storage.exec
        def fail(sql, *args):
            result = original(sql, *args)
            if sql.startswith('INSERT INTO sessions'): raise RuntimeError('injected')
            return result
        with patch.object(self.storage, 'exec', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'injected'): self.d.accept_invite(Q, token)
        self.assertEqual(len(self.d.members(session, family)), 1)
        bob = self.d.accept_invite(Q, token)
        self.assertEqual(self.d.authorize(bob, family)['role'], 'parent')

    def test_rollback_after_membership_insert(self):
        original = self.storage.exec
        def fail(sql, *args):
            result = original(sql, *args)
            if sql.startswith('INSERT INTO memberships'): raise RuntimeError('injected')
            return result
        with patch.object(self.storage, 'exec', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'injected'): self.d.create_family(P)
        self.assertEqual(self.storage.db.execute("SELECT count(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchone()[0], 0)
        self.d.create_family(P)
