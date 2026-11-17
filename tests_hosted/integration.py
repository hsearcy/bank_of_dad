"""Synthetic local Wrangler integration; no credentials or real DBs."""
import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import bank, db
from wrangler_helpers import isolated_environment, start_wrangler, stop

bank.time.time = lambda: 1700000000.0

def oracle():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript(db.SCHEMA)
    db._migrate(c)
    return c

def call(port, action, args=None, op='unused', family='a', status=200):
    query = urllib.parse.urlencode({'action': action, 'args': json.dumps(args or [], ensure_ascii=False), 'id': op, 'family': family})
    try:
        r = urllib.request.urlopen(f'http://127.0.0.1:{port}/?{query}', timeout=45)
    except urllib.error.HTTPError as e:
        r = e
    with r:
        body = json.load(r)
        assert r.status == status, (r.status, body, action)
        return body

def main():
    with tempfile.TemporaryDirectory(prefix='bod-foundation-') as temp:
        base = Path(temp)
        src = base / 'src'; src.mkdir()
        for f in ('bank.py', 'db.py', 'config.py'):
            shutil.copyfile(ROOT / f, src / f)
        shutil.copytree(ROOT / 'hosted', src / 'hosted', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(HERE / 'synthetic_worker.py', src / 'worker.py')
        (src / 'wrangler.jsonc').write_text(json.dumps({'name': 'bod-foundation-local-synthetic', 'main': 'worker.py', 'compatibility_date': '2026-09-01', 'compatibility_flags': ['python_workers', 'disable_python_external_sdk'], 'durable_objects': {'bindings': [{'name': 'BANK', 'class_name': 'SyntheticBank'}]}, 'migrations': [{'tag': 'v1', 'new_sqlite_classes': ['SyntheticBank']}]}))
        env = isolated_environment(base)
        c = oracle(); p = None
        logpath = base / 'wrangler.log'
        with logpath.open('w+') as log:
            try:
                p, port = start_wrangler(src, base, log, env)
                steps = [('add_kid', ['Synthetic', '🐣', '#123456'], bank.add_kid),
                         ('gift', [1, 500, ' chores 🧹 '], bank.gift),
                         ('save', [1, 400], bank.move_to_savings),
                         ('withdraw', [1, 100], bank.withdraw_savings),
                         ('spend', [1, 125, 'toy'], bank.spend),
                         ('invest', [1, '1mo', 200], bank.buy_bond)]
                for idx, (action, args, fn) in enumerate(steps):
                    actual = call(port, action, args, str(idx))['result']
                    expected = json.loads(json.dumps(fn(c, *args)))
                    assert actual == expected, (action, actual, expected)
                    actual_state = call(port, 'read', ['kids', []])['result']
                    assert actual_state == bank.all_kids_state(c), action
                    assert call(port, 'read', ['transactions', [1]])['result'] == bank.transactions(c, 1)
                assert call(port, 'add_kid', ['Other'], 'a', 'b')['result'] is True
                family_b = call(port, 'read', ['kids', []], family='b')['result']
                before = call(port, 'read', ['kids', []])['result']
                before_ledger = call(port, 'read', ['transactions', [1]])['result']
                assert 'after inner commit' in call(port, 'fail_after_commit', status=500)['error']
                assert call(port, 'read', ['kids', []])['result'] == before
                assert call(port, 'read', ['transactions', [1]])['result'] == before_ledger
                assert call(port, 'read', ['kids', []], family='b')['result'] == family_b
                stop(p); p = None
                p, port = start_wrangler(src, base, log, env)
                assert call(port, 'read', ['kids', []])['result'] == before
                assert call(port, 'read', ['transactions', [1]])['result'] == before_ledger
                assert call(port, 'read', ['kids', []], family='b')['result'] == family_b
                assert call(port, 'gift', [1, 500, ' chores 🧹 '], '1')['result'] is True
                assert call(port, 'read', ['kids', []])['result'] == before
                assert 'different payload' in call(port, 'gift', [1, 501], '1', status=500)['error']
                def add(i): return call(port, 'gift', [1, 1, 'concurrent'], 'c'+str(i))['result']
                with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
                    assert all(pool.map(add, range(12)))
                def retry(_): return call(port, 'gift', [1, 7, 'retry'], 'same')['result']
                with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
                    assert all(pool.map(retry, range(12)))
                for _ in range(12): bank.gift(c, 1, 1, 'concurrent')
                bank.gift(c, 1, 7, 'retry')
                assert call(port, 'read', ['kids', []])['result'] == bank.all_kids_state(c)
                assert call(port, 'read', ['transactions', [1]])['result'] == bank.transactions(c, 1)
                assert call(port, 'read', ['kids', []], family='b')['result'] == family_b
                stop(p); p = None
                p, port = start_wrangler(src, base, log, env)
                assert call(port, 'read', ['kids', []])['result'] == bank.all_kids_state(c)
                assert call(port, 'gift', [1, 7, 'retry'], 'same')['result'] is True
                assert call(port, 'read', ['kids', []])['result'] == bank.all_kids_state(c)
                assert not (base / 'forbidden.db').exists()
                print('PASS synthetic DO: SQLite oracle, rollback after inner commit, family isolation, restart, concurrent writes and retry')
            except BaseException:
                log.flush(); print(logpath.read_text(), file=sys.stderr)
                raise
            finally:
                stop(p); c.close()

if __name__ == '__main__': main()
