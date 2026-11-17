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
           'ledger': [dict(r) for r in c.execute('SELECT * FROM transactions ORDER BY id')],
           'history': [dict(r) for r in c.execute('SELECT * FROM history ORDER BY id')],
           'bonds': [dict(r) for r in c.execute('SELECT * FROM bonds ORDER BY id')],
           'settings': [dict(r) for r in c.execute('SELECT * FROM settings ORDER BY key')]}
    c.close()
    return out

def legacy_oracle():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript(LEGACY)
    def snap():
        return {'schema': [dict(r) for r in c.execute('SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name')],
                'tables': {table: {'columns': [dict(r) for r in c.execute('PRAGMA table_info(' + table + ')')],
                   'rows': [dict(r) for r in c.execute('SELECT * FROM ' + table + ' ORDER BY id')]}
           for table in ('kids', 'history', 'transactions')}}
    before = snap()
    db._migrate(c)
    first = snap()
    db._migrate(c)
    out = {'before': before, 'first': first, 'second': snap()}
    c.close()
    return out

def assert_legacy(migrated):
    before, first, second = (migrated[k] for k in ('before', 'first', 'second'))
    assert first == second, (first, second)  # Actual DO, not merely oracle parity.
    assert before != first
    expected_columns = {
        'kids': [('id', 'INTEGER', 0, None, 1), ('name', 'TEXT', 1, None, 0),
                 ('allowance_enabled', 'INTEGER', 1, '1', 0),
                 ('allowance_cents', 'INTEGER', 0, None, 0),
                 ('allowance_period_days', 'INTEGER', 0, None, 0)],
        'history': [('id', 'INTEGER', 0, None, 1), ('kid_id', 'INTEGER', 0, None, 0),
                    ('ts', 'REAL', 0, None, 0), ('networth_cents', 'INTEGER', 0, None, 0),
                    ('wallet_cents', 'INTEGER', 0, None, 0), ('savings_cents', 'INTEGER', 0, None, 0),
                    ('invest_cents', 'INTEGER', 0, None, 0)],
        'transactions': [('id', 'INTEGER', 0, None, 1), ('kid_id', 'INTEGER', 0, None, 0),
                         ('ts', 'REAL', 0, None, 0), ('kind', 'TEXT', 0, None, 0),
                         ('amount_cents', 'INTEGER', 0, None, 0), ('note', 'TEXT', 0, None, 0),
                         ('bond_id', 'INTEGER', 0, None, 0)]}
    expected_rows = {
        'kids': [{'id': 7, 'name': 'Legacy synthetic', 'allowance_enabled': 1,
                  'allowance_cents': 300, 'allowance_period_days': 14}],
        'history': [{'id': 1, 'kid_id': 7, 'ts': 123, 'networth_cents': 321,
                     'wallet_cents': None, 'savings_cents': None, 'invest_cents': None}],
        'transactions': [{'id': 1, 'kid_id': 7, 'ts': 123, 'kind': 'gift',
                          'amount_cents': 321, 'note': 'legacy', 'bond_id': None}]}
    original_lengths = {'kids': 2, 'history': 4, 'transactions': 6}
    for table, columns in expected_columns.items():
        def column_tuples(snapshot):
            return [(c['name'], c['type'], c['notnull'], c['dflt_value'], c['pk'])
                    for c in snapshot['tables'][table]['columns']]
        assert column_tuples(before) == columns[:original_lengths[table]]
        assert column_tuples(first) == columns
        assert first['tables'][table]['rows'] == expected_rows[table]
        assert before['tables'][table]['rows'] == [
            {k: row[k] for k, *_ in columns[:original_lengths[table]]}
            for row in expected_rows[table]]

EXPECTED_GIFT = {
    'kids': [{'id': 1, 'name': 'Synthetic', 'color': '#123456', 'emoji': '🐣',
              'wallet_cents': 1000, 'savings_cents': 0, 'allowance_enabled': 1,
              'allowance_cents': 300, 'allowance_period_days': 14,
              'allowance_anchor_ts': 1700000000, 'allowance_periods_paid': 0,
              'savings_accrued_ts': 1700000000, 'created_ts': 1700000000}],
    'ledger': [{'id': 1, 'kid_id': 1, 'ts': 1700000000, 'kind': 'gift',
                'amount_cents': 500, 'note': 'chores 🧹', 'bond_id': None}],
    'history': [{'id': 1, 'kid_id': 1, 'ts': 1700000000, 'networth_cents': 1000,
                 'wallet_cents': 1000, 'savings_cents': 0, 'invest_cents': 0}],
    'bonds': [], 'settings': []}

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
                assert after == EXPECTED_GIFT, (after, EXPECTED_GIFT)
                failed = request(port, 'fail', 500)
                assert 'injected after wallet update' in failed['error'], failed
                assert request(port, 'read')['result'] == after
                migrated = request(port, 'legacy')['result']
                assert_legacy(migrated)
                legacy_expected = legacy_oracle()
                assert migrated == legacy_expected, (migrated, legacy_expected)
                print('PASS fixed gift rows + raw SQLite parity, injected rollback, actual DO migration snapshots + concrete legacy expectations', flush=True)
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
