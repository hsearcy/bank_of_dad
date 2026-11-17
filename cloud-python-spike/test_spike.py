"""Runs only against disposable local Wrangler storage and an in-memory SQLite oracle."""
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import bank, db, config  # the repository originals, not spike copies
from fixtures import LEGACY

bank.time.time = lambda: 1700000000.0

def forbidden(*args, **kwargs):
    raise AssertionError('disk DB access forbidden in test')
db.get_conn = forbidden
db.init_db = forbidden
original_connect = sqlite3.connect
def memory_only(path, *args, **kwargs):
    if path != ':memory:':
        raise AssertionError('disk SQLite forbidden in oracle')
    return original_connect(path, *args, **kwargs)
sqlite3.connect = memory_only

def oracle():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys = ON')
    c.executescript(db.SCHEMA)
    db._migrate(c)
    bank.add_kid(c, 'Synthetic', '🐣', '#123456')
    assert bank.gift(c, 1, 500, '  chores 🧹  ') is True
    out = {'kids': [dict(r) for r in c.execute('SELECT * FROM kids ORDER BY id')],
           'ledger': bank.transactions(c, 1),
           'history': [dict(r) for r in c.execute('SELECT * FROM history ORDER BY id')]}
    c.close()
    return out

def legacy_oracle():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript(LEGACY)
    db._migrate(c)
    db._migrate(c)
    out = {table: {'columns': [dict(r) for r in c.execute('PRAGMA table_info(' + table + ')')],
                   'rows': [dict(r) for r in c.execute('SELECT * FROM ' + table + ' ORDER BY id')]}
           for table in ('kids', 'history', 'transactions')}
    c.close()
    return out

def request(port, action, expected=200):
    try:
        r = urllib.request.urlopen(f'http://127.0.0.1:{port}/{action}', timeout=20)
    except urllib.error.HTTPError as exc:
        r = exc
    with r:
        data = json.load(r)
        assert r.status == expected, (action, r.status, data)
        return data

def stop(p):
    if p is None:
        return
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        p.wait(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait(timeout=10)

def main():
    # Source watched by Wrangler is immutable; state, logs, caches, and DB guard
    # path are siblings, never inside its watched source directory.
    with tempfile.TemporaryDirectory(prefix='bod-python-spike-') as temp:
        base = Path(temp)
        src = base / 'src'
        src.mkdir()
        for name in ('bank.py', 'db.py', 'config.py'):
            shutil.copyfile(ROOT / name, src / name)
        for name in ('worker.py', 'fixtures.py', 'wrangler.jsonc'):
            shutil.copyfile(HERE / name, src / name)
        os.environ['BANK_OF_DAD_DB'] = str(base / 'forbidden.db')
        db.DB_PATH = os.environ['BANK_OF_DAD_DB']
        os.environ['WRANGLER_SEND_METRICS'] = 'false'
        os.environ['CI'] = '1'
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
        logpath = base / 'wrangler.log'
        p = None
        with logpath.open('w+') as log:
            def start():
                proc = subprocess.Popen([str(HERE / 'node_modules/.bin/wrangler'), 'dev',
                    '--config', str(src / 'wrangler.jsonc'), '--local', '--ip', '127.0.0.1',
                    '--port', str(port), '--persist-to', str(base / 'state')],
                    cwd=src, env=os.environ.copy(), stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True)
                try:
                    deadline = time.monotonic() + 120
                    while time.monotonic() < deadline:
                        if proc.poll() is not None:
                            raise RuntimeError('Wrangler exited early')
                        try:
                            with urllib.request.urlopen(f'http://127.0.0.1:{port}/read', timeout=2):
                                pass
                            return proc
                        except urllib.error.HTTPError:
                            return proc  # A missing schema also proves the worker is ready.
                        except OSError:
                            time.sleep(.5)
                    raise RuntimeError('Wrangler startup timed out')
                except BaseException:
                    stop(proc)
                    raise
            try:
                p = start()
                assert 'filesystem database access forbidden' in request(port, 'guard')['result']
                initial = request(port, 'init')
                assert initial['ok'], initial
                before = request(port, 'read')['result']
                assert before['kids'][0]['wallet_cents'] == config.STARTING_WALLET_CENTS and before['ledger'] == []
                assert request(port, 'gift')['result'] is True
                expected = oracle()
                after = request(port, 'read')['result']
                assert after == expected, (after, expected)
                failed = request(port, 'fail', 500)
                assert 'injected after wallet update' in failed['error'], failed
                assert request(port, 'read')['result'] == after
                migrated = request(port, 'legacy')['result']
                assert migrated == legacy_oracle(), (migrated, legacy_oracle())
                print('PASS add_kid + gift, exact SQLite oracle, injected rollback, legacy schema/row migration + idempotence', flush=True)
                stop(p)
                p = None
                p = start()
                assert request(port, 'read')['result'] == expected
                assert not (base / 'forbidden.db').exists()
                print('PASS restart persistence and forbidden filesystem DB guard', flush=True)
            except BaseException:
                log.flush()
                print(logpath.read_text(), file=sys.stderr)
                raise
            finally:
                stop(p)

if __name__ == '__main__':
    main()
