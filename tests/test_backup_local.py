import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tools import backup_local


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "family ?#.db"
        self.output = self.root / "backup.db"
        self.conn = sqlite3.connect(self.source)
        self.addCleanup(self.conn.close)
        self.conn.execute("CREATE TABLE family (name TEXT)")
        self.conn.execute("INSERT INTO family VALUES ('private synthetic name')")
        self.conn.commit()

    def test_wal_snapshot_integrity_private_and_source_unchanged(self):
        self.assertEqual(self.conn.execute("PRAGMA journal_mode=WAL").fetchone(), ("wal",))
        self.conn.execute("PRAGMA wal_autocheckpoint=0")
        self.conn.execute("INSERT INTO family VALUES ('committed in WAL')")
        self.conn.commit()
        main_before = self.source.read_bytes()
        wal = Path(str(self.source) + "-wal")
        wal_before = wal.read_bytes()
        backup_local.backup(self.source, self.output)
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
        with sqlite3.connect(self.output) as copy:
            self.assertEqual(copy.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
            self.assertEqual(copy.execute("SELECT name FROM family ORDER BY rowid").fetchall(),
                             [("private synthetic name",), ("committed in WAL",)])
        self.assertEqual(self.source.read_bytes(), main_before)
        self.assertEqual(wal.read_bytes(), wal_before)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM family").fetchone(), (2,))
        self.assertEqual(sorted(p.name for p in self.root.glob('.bod-backup-*')), [])

    def test_cli_missing_source_does_not_create_files(self):
        missing = self.root / "missing.db"
        script = Path(backup_local.__file__)
        result = subprocess.run([sys.executable, str(script), str(missing), str(self.output)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(missing.exists())
        self.assertFalse(self.output.exists())

    def test_existing_output_and_same_file_never_overwritten(self):
        self.output.write_bytes(b"keep me")
        with self.assertRaises(FileExistsError):
            backup_local.backup(self.source, self.output)
        self.assertEqual(self.output.read_bytes(), b"keep me")
        before = self.source.read_bytes()
        with self.assertRaises(FileExistsError):
            backup_local.backup(self.source, self.source)
        self.assertEqual(self.source.read_bytes(), before)
        alias = self.root / "alias"
        os.link(self.source, alias)
        with self.assertRaisesRegex(ValueError, 'multiple hardlinks'):
            backup_local.backup(self.source, alias)
        self.assertEqual(self.source.read_bytes(), before)

    def test_hardlinked_source_alias_rejected_before_any_output_or_sqlite_open(self):
        alias = self.root / 'alias.db'
        os.link(self.source, alias)
        before = self.source.read_bytes()
        rows = self.conn.execute('SELECT * FROM family').fetchall()
        for suffix in ('-journal', '-wal', '-shm'):
            output = Path(str(self.source) + suffix)
            with self.subTest(suffix=suffix):
                with mock.patch.object(backup_local.sqlite3, 'connect') as connect, \
                     mock.patch.object(backup_local.tempfile, 'TemporaryDirectory') as temp:
                    with self.assertRaisesRegex(ValueError, 'multiple hardlinks'):
                        backup_local.backup(alias, output)
                    connect.assert_not_called()
                    temp.assert_not_called()
                self.assertFalse(output.exists())
                self.assertEqual(self.source.read_bytes(), before)
                self.assertEqual(self.conn.execute('SELECT * FROM family').fetchall(), rows)
                self.assertEqual(list(self.root.glob('.bod-backup-*')), [])
        result = subprocess.run([sys.executable, backup_local.__file__, str(alias),
                                 str(self.source) + '-journal'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('multiple hardlinks', result.stderr)
        self.assertFalse(Path(str(self.source) + '-journal').exists())
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.conn.execute('SELECT * FROM family').fetchall(), rows)
        self.assertEqual({p.name for p in self.root.iterdir()}, {self.source.name, alias.name})

    def test_broken_symlink_output_is_not_replaced(self):
        self.output.symlink_to(self.root / "nonexistent")
        with self.assertRaises(FileExistsError):
            backup_local.backup(self.source, self.output)
        self.assertTrue(self.output.is_symlink())

    def test_invalid_database_cleans_temporary_file(self):
        bad = self.root / "bad.db"
        bad.write_bytes(b"not an sqlite database")
        result = subprocess.run([sys.executable, backup_local.__file__, str(bad), str(self.output)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("not an sqlite database", result.stderr)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob('.bod-backup-*')), [])

    def test_integrity_failure_prevents_publication_and_cleans_up(self):
        bad_result = mock.Mock()
        bad_result.execute.return_value.fetchall.return_value = [("corruption detail",)]
        with self.assertRaisesRegex(ValueError, "integrity check failed"):
            backup_local.check_integrity(bad_result)
        with mock.patch.object(backup_local, "check_integrity", side_effect=ValueError("failure")):
            with self.assertRaises(ValueError):
                backup_local.backup(self.source, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob('.bod-backup-*')), [])

    def test_publish_race_does_not_overwrite_and_cleans_temporary_file(self):
        original_link = os.link
        def competing_link(src, dst, **kwargs):
            self.output.write_bytes(b"other writer")
            return original_link(src, dst, **kwargs)
        with mock.patch.object(backup_local.os, "link", side_effect=competing_link):
            with self.assertRaises(FileExistsError):
                backup_local.backup(self.source, self.output)
        self.assertEqual(self.output.read_bytes(), b"other writer")
        self.assertEqual(list(self.root.glob('.bod-backup-*')), [])

    def test_publish_race_with_symlink_does_not_follow_or_replace_it(self):
        victim = self.root / 'target'
        victim.write_bytes(b'untouched')
        original_link = os.link
        def competing_link(src, dst, **kwargs):
            self.output.symlink_to(victim)
            return original_link(src, dst, **kwargs)
        with mock.patch.object(backup_local.os, 'link', side_effect=competing_link):
            with self.assertRaises(FileExistsError):
                backup_local.backup(self.source, self.output)
        self.assertTrue(self.output.is_symlink())
        self.assertEqual(victim.read_bytes(), b'untouched')
        self.assertEqual(list(self.root.glob('.bod-backup-*')), [])

    def test_temp_substitution_attack_cannot_replace_another_database(self):
        victim = self.root / 'victim.db'
        with sqlite3.connect(victim) as conn:
            conn.execute('CREATE TABLE secret (value TEXT)')
            conn.execute("INSERT INTO secret VALUES ('do not overwrite')")
        before = victim.read_bytes()
        original_mkstemp = tempfile.mkstemp
        def attacker(*args, **kwargs):
            fd, name = original_mkstemp(*args, **kwargs)
            directory = Path(name).parent
            if directory == self.root:
                os.unlink(name)
                os.symlink(victim, name)
            else:
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            return fd, name
        with mock.patch.object(backup_local.tempfile, 'mkstemp', side_effect=attacker):
            backup_local.backup(self.source, self.output)
        self.assertEqual(victim.read_bytes(), before)
        with sqlite3.connect(self.output) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM family').fetchone(), (1,))

    def test_source_sidecars_rejected_even_when_missing_or_aliased(self):
        alias_dir = self.root / 'alias-dir'
        alias_dir.symlink_to(self.root, target_is_directory=True)
        alias_source = self.root / 'alias-source'
        alias_source.symlink_to(self.source)
        for suffix in ('-wal', '-shm', '-journal'):
            for source, output in ((self.source, Path(str(self.source) + suffix)),
                                   (self.source, alias_dir / (self.source.name + suffix)),
                                   (alias_source, Path(str(self.source) + suffix)),
                                   (alias_source, Path(str(alias_source) + suffix))):
                with self.subTest(source=source, output=output):
                    with self.assertRaises((ValueError, FileExistsError)):
                        backup_local.backup(source, output)
                    self.assertFalse(output.exists())

    def test_missing_wal_sidecars_can_be_created_by_read_only_sqlite(self):
        self.assertEqual(self.conn.execute('PRAGMA journal_mode=WAL').fetchone(), ('wal',))
        self.conn.close()
        wal = Path(str(self.source) + '-wal')
        shm = Path(str(self.source) + '-shm')
        self.assertFalse(wal.exists())
        self.assertFalse(shm.exists())
        original_connect = sqlite3.connect
        observed = []
        def connect(*args, **kwargs):
            conn = original_connect(*args, **kwargs)
            if kwargs.get('uri'):
                conn.execute('SELECT count(*) FROM family').fetchone()
                observed.append((wal.exists(), shm.exists()))
            return conn
        with mock.patch.object(backup_local.sqlite3, 'connect', side_effect=connect):
            backup_local.backup(self.source, self.output)
        self.assertEqual(observed, [(True, True)])
        with sqlite3.connect(self.output) as copy:
            self.assertEqual(copy.execute('SELECT count(*) FROM family').fetchone(), (1,))
        with sqlite3.connect(self.source) as src:
            self.assertEqual(src.execute('SELECT count(*) FROM family').fetchone(), (1,))

    def test_existing_wal_sidecars_remain_intact(self):
        self.assertEqual(self.conn.execute('PRAGMA journal_mode=WAL').fetchone(), ('wal',))
        self.conn.execute("INSERT INTO family VALUES ('wal value')")
        self.conn.commit()
        for suffix in ('-wal', '-shm'):
            sidecar = Path(str(self.source) + suffix)
            self.assertTrue(sidecar.exists())
            before = sidecar.read_bytes()
            with self.assertRaises((ValueError, FileExistsError)):
                backup_local.backup(self.source, sidecar)
            self.assertEqual(sidecar.read_bytes(), before)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM family').fetchone(), (2,))

    def test_cli_success_does_not_print_database_content(self):
        result = subprocess.run([sys.executable, backup_local.__file__, str(self.source), str(self.output)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("private synthetic name", result.stdout + result.stderr)
        self.assertTrue(self.output.is_file())


if __name__ == "__main__":
    unittest.main()
