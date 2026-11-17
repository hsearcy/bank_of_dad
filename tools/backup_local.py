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


def backup(source, output):
    source = Path(source).absolute()
    output = Path(output).absolute()
    # Do not let SQLite create a database for a missing or non-file source.
    if not stat.S_ISREG(source.stat().st_mode):
        raise ValueError("source must be an existing regular file")
    # Includes broken symlinks. os.link below also enforces this atomically.
    if os.path.lexists(output):
        raise FileExistsError("output already exists")

    source_uri = source.as_uri() + "?mode=ro"
    temporary = None
    try:
        with closing(sqlite3.connect(source_uri, uri=True)) as src:
            fd, temporary = tempfile.mkstemp(prefix=".bod-backup-", dir=output.parent)
            os.close(fd)  # mkstemp creates the file with owner-only permissions.
            with closing(sqlite3.connect(temporary)) as dst:
                src.backup(dst)
                check_integrity(dst)
            # Publish only the completed file, without replacing any existing name.
            # Both paths are in the same directory, so this is an atomic link.
            os.link(temporary, output)
    finally:
        if temporary is not None:
            os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Make a private, consistent local SQLite backup.")
    parser.add_argument("source", help="existing SQLite database")
    parser.add_argument("output", help="new backup file (must not exist)")
    args = parser.parse_args(argv)
    try:
        backup(args.source, args.output)
    except (OSError, sqlite3.Error, ValueError) as exc:
        # Do not expose database content (including integrity-check diagnostics).
        print(f"Backup failed ({type(exc).__name__}); check output and temporary files.", file=sys.stderr)
        return 1
    print("Backup created and integrity checked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
