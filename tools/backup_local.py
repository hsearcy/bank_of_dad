#!/usr/bin/env python3
"""Create a private, consistent SQLite snapshot without opening the app."""
import argparse
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
from contextlib import closing


def check_integrity(connection):
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("backup integrity check failed")


class HardlinkedSourceError(ValueError):
    pass


def backup(source, output):
    source = Path(source).absolute()
    output = Path(output).absolute()
    # Do not let SQLite create a database for a missing or non-file source.
    source_stat = source.stat()
    if not stat.S_ISREG(source_stat.st_mode):
        raise ValueError("source must be an existing regular file")
    # Other hardlink names cannot be discovered by resolving this path. Their
    # SQLite sidecar names could otherwise be selected as the backup output.
    if source_stat.st_nlink != 1:
        raise HardlinkedSourceError("source has multiple hardlinks")
    # SQLite resolves source symlinks; protect both the given and resolved names.
    resolved_source = source.resolve(strict=True)
    resolved_output = output.resolve(strict=False)
    sidecars = {Path(str(name) + suffix).resolve(strict=False)
                for name in (source, resolved_source)
                for suffix in ('-wal', '-shm', '-journal')}
    if resolved_output in sidecars:
        raise ValueError("output must not be a source SQLite sidecar")
    # Includes broken symlinks; link's no-replacement rule is the actual guard.
    if os.path.lexists(output):
        raise FileExistsError("output already exists")

    source_uri = resolved_source.as_uri() + "?mode=ro"
    # The directory and its ancestors must be trusted against rename/substitution.
    # A 0700 directory prevents other users from replacing the SQLite temp file.
    with tempfile.TemporaryDirectory(prefix=".bod-backup-", dir=output.parent) as private:
        fd, temporary = tempfile.mkstemp(prefix="snapshot-", dir=private)
        os.close(fd)
        with closing(sqlite3.connect(source_uri, uri=True)) as src:
            with closing(sqlite3.connect(temporary)) as dst:
                src.backup(dst)
                check_integrity(dst)
        # Anchor both link endpoints to opened directories. link() creates a new
        # directory entry or fails; it never replaces/follows the final destination.
        parent_fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            private_fd = os.open(private, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.link(os.path.basename(temporary), output.name,
                        src_dir_fd=private_fd, dst_dir_fd=parent_fd,
                        follow_symlinks=False)
            finally:
                os.close(private_fd)
        finally:
            os.close(parent_fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Make a private, consistent local SQLite backup.")
    parser.add_argument("source", help="existing SQLite database")
    parser.add_argument("output", help="new backup file (must not exist)")
    args = parser.parse_args(argv)
    try:
        backup(args.source, args.output)
    except HardlinkedSourceError:
        print("Backup failed: source has multiple hardlinks; use a source with one link.", file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error, ValueError) as exc:
        # Do not expose database content (including integrity-check diagnostics).
        print(f"Backup failed ({type(exc).__name__}); check output and temporary files.", file=sys.stderr)
        return 1
    print("Backup created and integrity checked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
