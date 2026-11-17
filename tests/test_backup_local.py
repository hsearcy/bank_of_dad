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
        with self.assertRaises(FileExistsError):
            backup_local.backup(self.source, alias)
        self.assertEqual(self.source.read_bytes(), before)

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
        def competing_link(src, dst):
            self.output.write_bytes(b"other writer")
            return original_link(src, dst)
        with mock.patch.object(backup_local.os, "link", side_effect=competing_link):
            with self.assertRaises(FileExistsError):
                backup_local.backup(self.source, self.output)
        self.assertEqual(self.output.read_bytes(), b"other writer")
        self.assertEqual(list(self.root.glob('.bod-backup-*')), [])

    def test_cli_success_does_not_print_database_content(self):
        result = subprocess.run([sys.executable, backup_local.__file__, str(self.source), str(self.output)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("private synthetic name", result.stdout + result.stderr)
        self.assertTrue(self.output.is_file())


if __name__ == "__main__":
    unittest.main()
