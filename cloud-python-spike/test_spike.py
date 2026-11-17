"""Runs only against disposable local Wrangler storage and an in-memory SQLite oracle."""
import json
import re
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

# Imported checkout modules must not create __pycache__ outside scratch.
sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import bank, db, config  # the repository originals, not spike copies
from fixtures import LEGACY
from scenario import perform

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

def group_live(pgid):
    # Linux /proc includes reparented group members after Wrangler exits. Ignore
    # zombies: they hold no listeners and only their parent can reap them.
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            stat = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(stat[2]) == pgid and stat[0] != 'Z':
                return True
        except (FileNotFoundError, ProcessLookupError):
            continue
    return False


def stop(p):
    if p is None:
        return
    def send(sig):
        try:
            os.killpg(p.pid, sig)  # Only the session we created, never by port/name.
        except ProcessLookupError:
            pass
    def wait_group(seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            p.poll()  # Reap the leader, independently of its descendants.
            if not group_live(p.pid):
                return True
            time.sleep(.05)
        return False
    send(signal.SIGTERM)
    if not wait_group(15):
        send(signal.SIGKILL)
        if not wait_group(10):
            raise RuntimeError(f'process group {p.pid} did not exit')
    p.wait(timeout=1)

def isolated_environment(base):
    env = os.environ.copy()
    for key, path in {'HOME': base / 'home', 'XDG_CONFIG_HOME': base / 'config',
                      'XDG_CACHE_HOME': base / 'cache', 'WRANGLER_LOG_PATH': base / 'wrangler-logs',
                      'TMPDIR': base / 'tmp'}.items():
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    env.update(WRANGLER_SEND_METRICS='false', CI='1', BANK_OF_DAD_DB=str(base / 'forbidden.db'))
    return env


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def is_bind_failure(output, port):
    # Match the pinned Wrangler's actual proxy-bind diagnostic, not arbitrary
    # user/module output mentioning a collision. Other errors must not be hidden.
    output = re.sub(r'\x1b\[[0-9;]*m', '', output)
    errors = re.findall(r'^.*\[ERROR\].*$', output, re.MULTILINE)
    expected = (f'Address already in use (127.0.0.1:{port}). Please check that you are not '
                'already running a server on this address or specify a different port with --port.')
    return len(errors) == 1 and errors[0].strip() == '✘ [ERROR] ' + expected


def start_wrangler(src, base, log, env, choose_port=free_port, command=None, attempts=5):
    logpath = Path(log.name)
    diagnostics = []
    for attempt in range(attempts):
        port = choose_port()
        log.flush()
        offset = logpath.stat().st_size
        cmd = command or [str(HERE / 'node_modules/.bin/wrangler'), 'dev']
        proc = subprocess.Popen(cmd + [
            '--config', str(src / 'wrangler.jsonc'), '--local', '--ip', '127.0.0.1',
            '--port', str(port), '--inspector-ip', '127.0.0.1', '--inspector-port', '0',
            '--persist-to', str(base / 'state')], cwd=src, env=env, stdout=log,
            stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                output = logpath.read_bytes()[offset:].decode(errors='replace')
                if proc.poll() is not None:
                    # Read again after exit to include all of the failed attempt.
                    output = logpath.read_bytes()[offset:].decode(errors='replace')
                    if proc.returncode == 1 and is_bind_failure(output, port):
                        break
                    raise RuntimeError(f'Wrangler exited early (status {proc.returncode}):\n{output}')
                if f'Ready on http://127.0.0.1:{port}' in output:
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/read', timeout=2):
                            pass
                        return proc, port
                    except urllib.error.HTTPError:
                        return proc, port
                    except OSError:
                        pass
                time.sleep(.2)
            else:
                raise RuntimeError(f'Wrangler startup timed out:\n{output}')
        except BaseException:
            stop(proc)
            raise
        stop(proc)
        diagnostic = f'Bind collision on attempt {attempt + 1} (port {port}):\n{output}'
        diagnostics.append(diagnostic)
        print(diagnostic, file=sys.stderr, flush=True)
    raise RuntimeError('Wrangler bind collision retry limit exceeded:\n' + '\n'.join(diagnostics))


def main():
    # Source watched by Wrangler is immutable; state, logs, caches, and DB guard
    # path are siblings, never inside its watched source directory.
    with tempfile.TemporaryDirectory(prefix='bod-python-spike-') as temp:
        base = Path(temp)
        src = base / 'src'
        src.mkdir()
        for name in ('bank.py', 'db.py', 'config.py'):
            shutil.copyfile(ROOT / name, src / name)
        for name in ('worker.py', 'fixtures.py', 'scenario.py', 'wrangler.jsonc', 'post_commit_fixture.py'):
            shutil.copyfile(HERE / name, src / name)
        # Instrument only the disposable copy, never the checked-in worker or bank.
        worker_path = src / 'worker.py'
        worker = worker_path.read_text()
        for old, new in [
            ('from scenario import perform', 'from scenario import perform\nfrom post_commit_fixture import fail_after_collect_commit'),
            ('return perform(conn, action)', "return perform(conn, 'collect' if action == 'fail_after_commit' else action)"),
            ('proxy = create_proxy(operation)', "proxy = create_proxy(fail_after_collect_commit(operation, conn, snapshot) if action == 'fail_after_commit' else operation)"),
        ]:
            assert worker.count(old) == 1, old
            worker = worker.replace(old, new)
        worker_path.write_text(worker)
        env = isolated_environment(base)
        db.DB_PATH = env['BANK_OF_DAD_DB']
        port = None
        logpath = base / 'wrangler.log'
        p = None
        with logpath.open('w+') as log:
            def start():
                nonlocal port
                proc, port = start_wrangler(src, base, log, env)
                return proc
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
                # Independently initialized synthetic family; no auth is implied.
                assert request(port, 'family-b/init')['result'] is True
                family_b = request(port, 'family-b/read')['result']
                c = sqlite3.connect(':memory:')
                c.row_factory = sqlite3.Row
                c.execute('PRAGMA foreign_keys = ON')
                c.executescript(db.SCHEMA)
                db._migrate(c)
                bank.add_kid(c, 'Synthetic', '🐣', '#123456')
                bank.gift(c, 1, 500, '  chores 🧹  ')
                def oracle_snapshot():
                    return {key: [dict(row) for row in c.execute(f'SELECT * FROM {table} ORDER BY {order}')]
                            for key, table, order in [('kids', 'kids', 'id'), ('ledger', 'transactions', 'id'),
                                                      ('history', 'history', 'id'), ('bonds', 'bonds', 'id'),
                                                      ('settings', 'settings', 'key')]}
                for action, fixed in [('save', [True, 'Saved!']), ('withdraw', [True, 'Done!']),
                                      ('spend', [True, 'Spent!']), ('invest', [True, 'Locked in!']),
                                      ('collect_invest', 0),
                                      ('advance', None), ('pending', {'pending_allowance_cents': 600,
                                       'pending_interest_cents': 51, 'pending_bonds_cents': 50, 'owned_cents': 875})]:
                    actual = request(port, action)['result']
                    reference = json.loads(json.dumps(perform(c, action)))
                    assert actual == reference == fixed, (action, actual, reference, fixed)
                    assert request(port, 'read')['result'] == oracle_snapshot(), action
                    assert request(port, 'family-b/read')['result'] == family_b, action
                before_collect = request(port, 'read')['result']
                failed = request(port, 'fail_collect', 500)
                assert 'injected after collect writes' in failed['error'], failed
                assert request(port, 'read')['result'] == before_collect
                failed = request(port, 'fail_after_commit', 500)
                assert failed['ok'] is False and failed['error'] == 'injected after inner commit returned', failed
                assert request(port, 'read')['result'] == before_collect
                assert request(port, 'family-b/read')['result'] == family_b
                # Restart before any successful retry: no phantom write may persist.
                stop(p)
                p = None
                p = start()
                assert request(port, 'read')['result'] == before_collect
                assert request(port, 'family-b/read')['result'] == family_b
                assert request(port, 'pending')['result'] == {'pending_allowance_cents': 600,
                    'pending_interest_cents': 51, 'pending_bonds_cents': 50, 'owned_cents': 875}
                print('PASS post-inner-commit failure: complete snapshot unchanged immediately and after restart', flush=True)
                assert request(port, 'collect')['result'] == perform(c, 'collect') == 701
                expected = oracle_snapshot()
                assert request(port, 'read')['result'] == expected
                assert (expected['kids'][0]['wallet_cents'], expected['kids'][0]['savings_cents']) == (1225, 351)
                assert [(t['kind'], t['amount_cents'], t['bond_id']) for t in expected['ledger']] == [
                    ('gift', 500, None), ('save', 400, None), ('withdraw', 100, None),
                    ('spend', -125, None), ('invest', 200, 1), ('allowance', 600, None),
                    ('interest', 51, None), ('bond_payout', 250, 1)]
                assert expected['bonds'][0]['collected'] == 1
                assert request(port, 'collect')['result'] == perform(c, 'collect') == 0
                assert request(port, 'read')['result'] == expected
                assert request(port, 'family-b/read')['result'] == family_b
                c.close()
                print('PASS save, withdraw, spend, accrual, investment payout, rollback and family isolation', flush=True)
                print('PASS fixed gift rows + raw SQLite parity, injected rollback, actual DO migration snapshots + concrete legacy expectations', flush=True)
                stop(p)
                p = None
                p = start()
                assert request(port, 'read')['result'] == expected
                assert request(port, 'family-b/read')['result'] == family_b
                assert request(port, 'family-b/gift')['result'] is True
                assert request(port, 'family-b/read')['result'] == EXPECTED_GIFT
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
