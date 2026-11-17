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
        self.auth.bootstrap_privileged(F, OWNER)
        self.token = self.auth.issue_grant_privileged(F, OWNER, 100)
        self.revision = module.RevisionFixture(self.auth)
        self.revision.install_for_fresh_fixture(F, self.token)
        # Synthetic bootstrap only, before exposing the fixture.
        self.auth.mutate(F, self.token, 'kid', 'add_kid', ['Synthetic'])

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
        self.assertEqual(before['revision'], 0)
        self.assertEqual(f.mutate(F, self.token, 'gift', 'gift', [1, 100]), (True, 1, True))
        self.assertEqual(f.mutate(F, self.token, 'gift', 'gift', [1, 100]), (True, 1, False))
        self.assertEqual(f.mutate(F, self.token, 'empty', 'spend', [1, 600]), ([True, 'Spent!'], 2, True))
        result, rev, changed = f.mutate(F, self.token, 'small', 'save', [1, 1])
        self.assertFalse(result[0]); self.assertEqual(rev, 2); self.assertFalse(changed)
        full = self.full()
        with self.assertRaisesRegex(RuntimeError, 'synthetic fault'):
            f.mutate(F, self.token, 'fault', 'gift', [1, 50], fault=True)
        self.assertEqual(self.full(), full)
        with self.assertRaises(ValueError): f.mutate(F, self.token, 'gift', 'gift', [1, 101])
        self.assertEqual(self.full(), full)
        self.assertEqual(f.snapshot(F, self.token)['revision'], 2)
        with self.assertRaises(PermissionError): f.snapshot('b' * 32, self.token)
        with self.assertRaises(PermissionError): f.mutate('b' * 32, self.token, 'bad', 'gift', [1, 10])
        self.assertEqual(self.full(), full)

    def test_unsupported_marker_fails_closed(self):
        self.storage.db.execute('UPDATE fixture_revision SET version=2')
        full = self.full()
        with self.assertRaises(RuntimeError): self.revision.snapshot(F, self.token)
        with self.assertRaises(RuntimeError): self.revision.mutate(F, self.token, 'x', 'gift', [1, 10])
        with self.assertRaises(RuntimeError): self.revision.install_for_fresh_fixture(F, self.token)
        self.assertEqual(self.full(), full)

if __name__ == '__main__': unittest.main()
