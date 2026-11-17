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
                # Separate synthetic families exercise SQL-side checks in the real DO bridge.
                call(port, 'fixture_view', family='view')
                assert 'unversioned' in call(port, 'read', ['kids', []], family='view', status=500)['error']
                call(port, 'add_kid', ['Bounds'], 'kid', 'bounds')
                call(port, 'fixture_balance', ['oversized'], family='bounds')
                assert 'safe limits' in call(port, 'gift', [1, 1], 'overflow', 'bounds', status=500)['error']
                assert call(port, 'fixture_snapshot', family='bounds')['result']['wallet'] == '9007199254740992'
                call(port, 'fixture_balance', ['near'], family='bounds')
                assert call(port, 'gift', [1, 1, ' x '], 'last', 'bounds')['result'] is True
                assert 'different payload' in call(port, 'gift', [1, 1, 'x'], 'last', 'bounds', status=500)['error']
                assert call(port, 'gift', [1, 1, ' x '], 'last', 'bounds')['result'] is True
                snapshot = call(port, 'fixture_snapshot', family='bounds')['result']
                assert 'safe limits' in call(port, 'gift', [1, 1], 'too-far', 'bounds', status=500)['error']
                assert call(port, 'fixture_snapshot', family='bounds')['result'] == snapshot
                for action, args in [('save', [1, 100]), ('withdraw', [1, 100]), ('spend', [1, 1]), ('invest', [1, '1mo', 100])]:
                    assert call(port, action, args, action, 'bounds')['result'][0] is True
                assert call(port, 'read', ['transactions', [1, 101]], family='bounds', status=500)
                assert call(port, 'read', [[], []], family='bounds', status=500)
                assert 'invalid request' in call(port, 'save', [99, 1], 'missing', 'bounds', status=500)['error']
                call(port, 'add_kid', ['Accrual'], 'kid', 'accrual')
                call(port, 'fixture_balance', ['accrual'], family='accrual')
                assert 'safe limits' in call(port, 'withdraw', [1, 1], 'w', 'accrual', status=500)['error']
                assert call(port, 'fixture_snapshot', family='accrual')['result']['savings'] == '1000000000'
                call(port, 'fixture_balance', ['investment'], family='accrual')
                before_invest = call(port, 'fixture_snapshot', family='accrual')['result']
                assert 'safe limits' in call(port, 'invest', [1, '3mo', 1000000000], 'i', 'accrual', status=500)['error']
                assert call(port, 'fixture_snapshot', family='accrual')['result'] == before_invest
                for existing in (False, True):
                    family = 'v0-' + str(existing)
                    call(port, 'fixture_v0', [existing], family=family)
                    assert 'unsupported schema version' in call(port, 'read', ['kids', []], family=family, status=500)['error']
                # Independent family and SQLite oracle for each collection mode.
                for kind in ('allowance', 'interest', 'invest', 'all'):
                    family = 'reward-' + kind
                    r = oracle()
                    try:
                        for action, args, fn in [('add_kid', ['Rewards'], bank.add_kid), ('save', [1, 200], bank.move_to_savings), ('invest', [1, '1mo', 200], bank.buy_bond)]:
                            assert call(port, action, args, action, family)['result'] == json.loads(json.dumps(fn(r, *args)))
                        call(port, 'fixture_rewards', family=family)
                        r.execute('UPDATE kids SET allowance_anchor_ts=1696976000, savings_accrued_ts=1696976000 WHERE id=1')
                        r.execute('UPDATE bonds SET buy_ts=1696976000, mature_ts=1699568000 WHERE kid_id=1')
                        before_reward = call(port, 'read', ['kids', []], family=family)['result']
                        assert before_reward == bank.all_kids_state(r)
                        assert 'after collection commit' in call(port, 'fail_collect', family=family, status=500)['error']
                        assert call(port, 'read', ['kids', []], family=family)['result'] == before_reward
                        expected = bank.collect_rewards(r, 1, kind)
                        assert expected > 0
                        def collect(_): return call(port, 'collect_rewards', [1, kind], 'same', family)['result']
                        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                            assert list(pool.map(collect, range(8))) == [expected] * 8
                        assert call(port, 'collect_rewards', [1, kind], 'new', family)['result'] == 0
                        assert call(port, 'read', ['kids', []], family=family)['result'] == bank.all_kids_state(r)
                        assert call(port, 'read', ['transactions', [1]], family=family)['result'] == bank.transactions(r, 1)
                    finally:
                        r.close()
                assert call(port, 'read', ['kids', []], family='b')['result'] == family_b
                call(port, 'add_kid', ['Concurrent rewards'], 'kid', 'reward-race')
                call(port, 'fixture_rewards', family='reward-race')
                def distinct_collect(i):
                    return call(port, 'collect_rewards', [1, 'allowance'], 'race-' + str(i), 'reward-race')['result']
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                    results = list(pool.map(distinct_collect, range(8)))
                assert len([x for x in results if x > 0]) == 1 and results.count(0) == 7, results
                stop(p); p = None
                p, port = start_wrangler(src, base, log, env)
                assert call(port, 'collect_rewards', [1, 'all'], 'same', 'reward-all')['result'] > 0
                assert call(port, 'collect_rewards', [1, 'all'], 'after-restart', 'reward-all')['result'] == 0
                assert call(port, 'collect_rewards', [1, 'allowance'], 'later', 'reward-race')['result'] == 0
                call(port, 'add_kid', ['Bad bond'], 'kid', 'bad-bond')
                call(port, 'invest', [1, '1mo', 100], 'bond', 'bad-bond')
                call(port, 'fixture_bad_bond', family='bad-bond')
                for action, args in [('read', ['kids', []]), ('collect_rewards', [1]), ('gift', [1, 1])]:
                    assert 'safe limits' in call(port, action, args, 'bad', 'bad-bond', status=500)['error']
                call(port, 'add_kid', ['Reversed bond'], 'kid', 'reversed-bond')
                call(port, 'invest', [1, '1mo', 100], 'bond', 'reversed-bond')
                call(port, 'fixture_reversed_bond', family='reversed-bond')
                before_corrupt = call(port, 'fixture_full_snapshot', family='reversed-bond')['result']
                for action, args in [('read', ['kids', []]), ('collect_rewards', [1, 'invest']), ('gift', [1, 1])]:
                    assert 'safe limits' in call(port, action, args, 'rejected', 'reversed-bond', status=500)['error']
                    assert call(port, 'fixture_full_snapshot', family='reversed-bond')['result'] == before_corrupt
                assert not (base / 'forbidden.db').exists()
                print('PASS synthetic DO: SQLite oracle, rollback after inner commit, family isolation, restart, concurrent writes and retry')
            except BaseException:
                log.flush(); print(logpath.read_text(), file=sys.stderr)
                raise
            finally:
                stop(p); c.close()

if __name__ == '__main__': main()
