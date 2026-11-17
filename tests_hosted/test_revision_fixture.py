"""Unit-only prototype: no runtime or browser integration is claimed."""
import importlib.util
from pathlib import Path
import unittest

from test_foundation import Storage
from hosted.family_auth import FamilyAuthorization

spec = importlib.util.spec_from_file_location('revision_fixture', Path(__file__).resolve().parents[1] / 'spike/sync-revision/revision.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

F = 'a' * 32
OWNER = {'issuer': 'https://synthetic.invalid', 'subject': 'owner'}

class RevisionFixtureTest(unittest.TestCase):
    def setUp(self):
        self.storage = Storage()
        self.addCleanup(self.storage.db.close)
        self.auth = FamilyAuthorization(self.storage, lambda x: x, lambda: 1000)
        self.revision = module.RevisionFixture(self.auth)
        self.revision.bootstrap_fresh(F, OWNER)
        self.token = self.auth.issue_grant_privileged(F, OWNER, 100)
        self.assertEqual(self.revision.mutate(F, self.token, 'kid', 'add_kid', ['Synthetic']), (True, 1, True))

    def full(self):
        db = self.storage.db
        schema = [tuple(r) for r in db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
        tables = {}
        for typ, name, _, _ in schema:
            if typ == 'table':
                tables[name] = sorted((tuple(r) for r in db.execute('SELECT * FROM "' + name.replace('"', '""') + '"')), key=repr)
        return schema, tables

    def test_commit_replay_failure_and_rollback(self):
        f = self.revision
        before = f.snapshot(F, self.token)
        self.assertEqual(before['revision'], 1)
        self.assertEqual(f.mutate(F, self.token, 'gift', 'gift', [1, 100]), (True, 2, True))
        self.assertEqual(f.mutate(F, self.token, 'gift', 'gift', [1, 100]), (True, 2, False))
        self.assertEqual(f.mutate(F, self.token, 'empty', 'spend', [1, 600]), ([True, 'Spent!'], 3, True))
        result, rev, changed = f.mutate(F, self.token, 'small', 'save', [1, 1])
        self.assertFalse(result[0]); self.assertEqual(rev, 3); self.assertFalse(changed)
        full = self.full()
        with self.assertRaisesRegex(RuntimeError, 'synthetic fault'):
            f.mutate(F, self.token, 'fault', 'gift', [1, 50], fault=True)
        self.assertEqual(self.full(), full)
        with self.assertRaises(ValueError): f.mutate(F, self.token, 'gift', 'gift', [1, 101])
        self.assertEqual(self.full(), full)
        self.assertEqual(f.snapshot(F, self.token)['revision'], 3)
        with self.assertRaises(PermissionError): f.snapshot('b' * 32, self.token)
        with self.assertRaises(PermissionError): f.mutate('b' * 32, self.token, 'bad', 'gift', [1, 10])
        self.assertEqual(self.full(), full)

    def test_unsupported_marker_fails_closed(self):
        self.storage.db.execute('UPDATE fixture_revision SET version=2')
        full = self.full()
        with self.assertRaises(RuntimeError): self.revision.snapshot(F, self.token)
        with self.assertRaises(RuntimeError): self.revision.mutate(F, self.token, 'x', 'gift', [1, 10])
        with self.assertRaises(RuntimeError): self.revision.bootstrap_fresh(F, OWNER)
        self.assertEqual(self.full(), full)

class RevisionBootstrapTest(unittest.TestCase):
    def storage(self):
        storage = Storage()
        self.addCleanup(storage.db.close)
        auth = FamilyAuthorization(storage, lambda x: x, lambda: 1000)
        return storage, auth, module.RevisionFixture(auth)

    def test_atomic_fault_rolls_back_all_schema(self):
        storage, auth, revision = self.storage()
        before = storage.db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master').fetchall()
        with self.assertRaisesRegex(RuntimeError, 'bootstrap fault'):
            revision.bootstrap_fresh(F, OWNER, fault=True)
        self.assertEqual(storage.db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master').fetchall(), before)
        revision.bootstrap_fresh(F, OWNER)
        token = auth.issue_grant_privileged(F, OWNER, 100)
        self.assertEqual(revision.snapshot(F, token)['revision'], 0)

    def test_existing_unrevisioned_and_view_only_are_not_adopted(self):
        for kind in ('unrevisioned', 'view'):
            with self.subTest(kind=kind):
                storage, auth, revision = self.storage()
                if kind == 'unrevisioned':
                    auth.bootstrap_privileged(F, OWNER)
                else:
                    storage.db.execute('CREATE VIEW unrelated AS SELECT 1 AS x')
                before = storage.db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
                with self.assertRaisesRegex(RuntimeError, 'fresh empty'):
                    revision.bootstrap_fresh(F, OWNER)
                self.assertEqual(storage.db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall(), before)
                if kind == 'unrevisioned':
                    token = auth.issue_grant_privileged(F, OWNER, 100)
                    with self.assertRaises(RuntimeError):
                        revision.snapshot(F, token)
                    with self.assertRaises(RuntimeError):
                        revision.mutate(F, token, 'x', 'add_kid', ['Synthetic'])


if __name__ == '__main__': unittest.main()
