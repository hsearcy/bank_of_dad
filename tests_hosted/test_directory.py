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

    def test_modified_v1_schema_rejected_before_authorization_or_mutation(self):
        mutations = [
            'DROP TABLE invitations',
            'ALTER TABLE memberships ADD COLUMN extra TEXT',
            'CREATE TRIGGER evil AFTER INSERT ON sessions BEGIN DELETE FROM memberships; END',
            'CREATE INDEX extra_index ON memberships(subject)',
            'DROP TABLE sessions',
        ]
        for statement in mutations:
            with self.subTest(statement=statement):
                storage = Storage()
                try:
                    d = Directory(storage, proxy_factory=lambda cb: cb, clock=lambda: 1000)
                    family, session = d.create_family(P)
                    storage.db.execute(statement)
                    before = list(storage.db.iterdump())
                    with self.assertRaises(RuntimeError): d.authorize(session, family)
                    with self.assertRaises(RuntimeError): d.create_family(Q)
                    self.assertEqual(list(storage.db.iterdump()), before)
                finally:
                    storage.db.close()

    def test_rebuilt_table_without_constraints_rejected(self):
        family, session = self.d.create_family(P)
        self.storage.db.execute('ALTER TABLE memberships RENAME TO old_memberships')
        self.storage.db.execute('CREATE TABLE memberships (family TEXT, issuer TEXT, subject TEXT, role TEXT, status TEXT)')
        self.storage.db.execute('INSERT INTO memberships SELECT * FROM old_memberships')
        self.storage.db.execute('DROP TABLE old_memberships')
        with self.assertRaises(RuntimeError): self.d.authorize(session, family)

    def test_unsupported_versions_and_interrupted_initialization(self):
        original = self.storage.exec
        def fail(sql, *args):
            result = original(sql, *args)
            if sql.startswith('CREATE TABLE sessions'): raise RuntimeError('injected')
            return result
        with patch.object(self.storage, 'exec', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'injected'): self.d.create_family(P)
        self.assertEqual(self.storage.db.execute("SELECT count(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchone()[0], 0)
        family, session = self.d.create_family(P)
        for version in (0, 2):
            self.storage.db.execute('UPDATE directory_schema SET version=?', (version,))
            with self.assertRaisesRegex(RuntimeError, 'unsupported'): self.d.authorize(session, family)
        self.storage.db.execute('UPDATE directory_schema SET version=1')
        self.assertEqual(self.d.authorize(session, family)['role'], 'owner')
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
