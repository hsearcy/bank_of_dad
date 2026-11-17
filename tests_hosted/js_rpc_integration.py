"""Local, synthetic JS Worker -> Python FamilyBank DO integration; never deploy."""
import concurrent.futures
import json
import shutil
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from wrangler_helpers import WRANGLER, isolated_environment, start_wrangler, stop

ROOT = Path(__file__).resolve().parent.parent
F = 'a' * 32

def main():
    with tempfile.TemporaryDirectory(prefix='bod-js-rpc-') as tmp:
        base = Path(tmp); src = base / 'src'; src.mkdir()
        for name in ('bank.py', 'db.py', 'config.py'):
            shutil.copyfile(ROOT / name, src / name)
        shutil.copytree(ROOT / 'hosted', src / 'hosted', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(ROOT / 'tests_hosted/synthetic_js_bank.py', src / 'worker.py')
        shutil.copyfile(ROOT / 'tests_hosted/synthetic_js_caller.js', src / 'caller.js')
        (src / 'wrangler.jsonc').write_text(json.dumps({'name':'bod-js-bank-synthetic', 'main':'worker.py', 'compatibility_date':'2026-09-01', 'compatibility_flags':['python_workers','disable_python_external_sdk'], 'durable_objects':{'bindings':[{'name':'BANK','class_name':'SyntheticJSBank'}]}, 'migrations':[{'tag':'v1','new_sqlite_classes':['SyntheticJSBank']}]}))
        (src / 'caller.jsonc').write_text(json.dumps({'name':'bod-js-caller-synthetic', 'main':'caller.js', 'compatibility_date':'2026-09-01', 'durable_objects':{'bindings':[{'name':'BANK','class_name':'SyntheticJSBank','script_name':'bod-js-bank-synthetic'}]}}))
        p = None
        with (base / 'wrangler.log').open('w+') as log:
            try:
                p, port = start_wrangler(src, base, log, isolated_environment(base), command=[WRANGLER, 'dev', '-c', str(src / 'caller.jsonc')])
                def call(method, args=(), status=200, **extra):
                    body = json.dumps({'method':method, 'args':args, **extra}).encode()
                    req = urllib.request.Request(f'http://127.0.0.1:{port}/', data=body, headers={'content-type':'application/json'})
                    try: r = urllib.request.urlopen(req, timeout=60)
                    except urllib.error.HTTPError as e: r = e
                    with r:
                        raw = r.read().decode(); data = json.loads(raw)
                        assert r.status == status, (method, r.status, data)
                        return data
                tokens = json.loads(call('setup')['result']); token = tokens['active']
                other_family = 'b' * 32
                other = json.loads(call('setup', [other_family], object='other')['result'])
                assert other['active'] != token
                payload = {'nested': {'array': [None, True, False, 'Zoë 🐉', 0, -42, 1.25,
                                                {'null': None, 'deeper': [[], {}, [False, None]]}]},
                           'timestamp': 2_001_209_600, 'empty': '', 'nil': None}
                echoed = call('echo', value=payload)['result']
                # JSON comparison distinguishes booleans from numbers, unlike Python ==.
                canonical = lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
                assert canonical(echoed) == canonical(payload), echoed
                assert call('mutate', [other_family, other['active'], 'other-add', 'add_kid',
                                       ['Other', '🌙', '#654321']], object='other')['result'] is True
                assert call('read', [other_family, other['active'], 'kids', []], object='other')['result'][0]['name'] == 'Other'
                def both_snapshots():
                    return (call('snapshot')['result'], call('snapshot', object='other')['result'])
                isolated_before = both_snapshots()
                for obj, family, wrong in [('other', other_family, token), ('synthetic', F, other['active'])]:
                    for method, args in [('read', [family, wrong, 'kids', []]),
                                         ('mutate', [family, wrong, 'cross', 'gift', [1, 10]])]:
                        assert call(method, args, object=obj, status=400) == {'error': 'denied'}
                        assert both_snapshots() == isolated_before
                # Also prove family and token cannot be routed together to the other object.
                for obj, family, secret in [('other', F, token), ('synthetic', other_family, other['active'])]:
                    assert call('read', [family, secret, 'kids', []], object=obj, status=400) == {'error': 'denied'}
                    assert both_snapshots() == isolated_before
                def mutate(op, action, args):
                    return call('mutate', [F, token, op, action, args])['result']
                def read(action, args):
                    return call('read', [F, token, action, args])['result']
                def snap(): return call('snapshot')['result']
                assert mutate('add', 'add_kid', ['Zoë 🐉', '🦊', '#123456']) is True
                kids = read('kids', [])
                assert len(kids) == 1
                kid = kids[0]
                expected = {'id': 1, 'name': 'Zoë 🐉', 'color': '#123456', 'emoji': '🦊',
                            'wallet_cents': 500, 'savings_cents': 0, 'invest_cents': 0,
                            'owned_cents': 500, 'pending_cents': 0, 'pending_allowance_cents': 0,
                            'pending_interest_cents': 0, 'pending_bonds_cents': 0,
                            'allowance_enabled': True, 'allowance_cents': 300,
                            'allowance_period_days': 14, 'bonds': [], 'history': []}
                assert set(kid) == set(expected) | {'next_allowance_ts'}, kid
                assert {k: kid[k] for k in expected} == expected
                assert type(kid['allowance_enabled']) is bool
                assert all(type(kid[k]) is int for k in ('id', 'wallet_cents', 'savings_cents', 'pending_cents'))
                assert type(kid['next_allowance_ts']) in (int, float) and kid['next_allowance_ts'] == 2_001_209_600
                assert mutate('gift', 'gift', [1, 100, 'hello 🦊']) is True
                assert mutate('save', 'save', [1, 10]) == [True, 'Saved!']
                assert mutate('withdraw', 'withdraw', [1, 1]) == [True, 'Done!']
                assert mutate('spend', 'spend', [1, 1, '🛒']) == [True, 'Spent!']
                assert mutate('invest', 'invest', [1, '1mo', 4]) == [True, 'Locked in!']
                call('seed_allowance')
                assert read('kids', [])[0]['pending_allowance_cents'] == 300
                assert mutate('collect', 'collect_rewards', [1, 'allowance']) == 300
                rows = read('transactions', [1])
                before = snap()
                assert len(rows) == 6
                assert [(r['id'], r['kind'], r['amount_cents'], r['note']) for r in rows] == [
                    (6, 'allowance', 300, 'Allowance'), (5, 'invest', 4, 'Invested (1 Month lock)'), (4, 'spend', -1, '🛒'),
                    (3, 'withdraw', 1, 'Savings to wallet'), (2, 'save', 10, 'Moved to savings'),
                    (1, 'gift', 100, 'hello 🦊')]
                source = {r['id']: r for r in before['transactions']}
                for row in rows:
                    assert set(row) == {'id', 'ts', 'kind', 'amount_cents', 'note'}
                    assert type(row['id']) is int and type(row['amount_cents']) is int
                    assert type(row['ts']) in (int, float) and row['ts'] == source[row['id']]['ts']
                assert before['transactions'][0]['bond_id'] is None
                populated = read('kids', [])[0]
                for secret in tokens.values():
                    assert secret not in json.dumps(populated, ensure_ascii=False)
                    assert secret not in json.dumps(rows, ensure_ascii=False)
                assert 'synthetic-owner' not in json.dumps(populated, ensure_ascii=False)
                assert populated['wallet_cents'] == 886 and populated['savings_cents'] == 9
                assert len(populated['bonds']) == 1 and len(populated['history']) == 6
                bond = populated['bonds'][0]; source_bond = before['bonds'][0]
                assert set(bond) == {'id', 'term', 'emoji', 'principal_cents', 'payout_cents', 'buy_ts', 'mature_ts', 'days_left', 'ready'}
                assert (bond['id'], bond['term'], bond['principal_cents'], bond['payout_cents'], bond['ready']) == (1, '1 Month', 4, 5, False)
                assert type(bond['ready']) is bool and type(bond['days_left']) is int
                assert bond['buy_ts'] == source_bond['buy_ts'] and bond['mature_ts'] == source_bond['mature_ts']
                expected_history = [{'ts': h['ts'], 'v': h['networth_cents'], 'wallet': h['wallet_cents'],
                                     'savings': h['savings_cents'], 'invest': h['invest_cents']}
                                    for h in before['history']]
                assert populated['history'] == sorted(expected_history, key=lambda h: h['ts'])
                for history in populated['history']:
                    assert all(type(history[k]) is int for k in ('v', 'wallet', 'savings', 'invest'))
                for method, args, extra, category in [
                    ('read', ['b'*32, token, 'kids', []], {}, 'denied'),
                    ('read', [F, 'not-a-token', 'kids', []], {}, 'denied'),
                    ('read', [F, tokens['expired'], 'kids', []], {}, 'denied'),
                    ('read', [F, tokens['revoked'], 'kids', []], {}, 'denied'),
                    ('mutate', [F, tokens['revoked'], 'bad', 'gift', [1, 10]], {}, 'denied'),
                    ('mutate', [F, tokens['expired'], 'bad', 'gift', [1, 10]], {}, 'denied'),
                    ('mutate', ['b'*32, token, 'bad', 'gift', [1, 10]], {}, 'denied'),
                    ('mutate', [F, 'not-a-token', 'bad', 'gift', [1, 10]], {}, 'denied'),
                    ('mutate', [F, token, 'bad', 'gift', [1, True]], {}, 'invalid_request'),
                    ('mutate', [F, token, 'bad', 'gift', [1, 1.5]], {}, 'invalid_request'),
                    ('mutate', [F, token, 'gift', 'gift', [1, 101]], {}, 'invalid_request'),
                    ('attempt', [F, {}, 100], {'name':'issue_grant_privileged'}, 'missing_method'),
                    ('attempt', [], {'name':'_authorization'}, 'missing_method'),
                    ('attempt', [F, token, 'kids', []], {'name':'_read'}, 'missing_method'),
                    ('attempt', ['op', 'gift', [1, 2]], {'name':'mutate'}, 'bad_signature'),
                    ('mutate', [F, token, 'bad-action', '_mutate', [1, 2]], {}, 'invalid_request'),
                ]:
                    response = call(method, args, status=400, **extra)
                    assert response == {'error': category}, (method, extra, response, category)
                    for secret in tokens.values(): assert secret not in json.dumps(response)
                    assert 'Traceback' not in json.dumps(response)
                    assert snap() == before
                # Observe raw runtime behavior without returning exception text to HTTP.
                assert call('read', [F, 'not-a-token', 'kids', []], status=400,
                            diagnose_traceback=True) == {'error': 'denied', 'raw_has_traceback': True}
                fault_args = [F, token, 'fault', 'gift', [1, 7, 'retry']]
                assert call('fault', fault_args, status=400) == {'error':'injected_fault'}
                assert snap() == before
                assert call('mutate', fault_args)['result'] is True
                after = snap()
                assert call('mutate', fault_args)['result'] is True
                assert snap() == after
                def parallel_gift(op): return mutate(op, 'gift', [1, 1, 'parallel'])
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                    assert list(pool.map(parallel_gift, ['same'] * 8)) == [True] * 8
                    assert list(pool.map(parallel_gift, ['parallel-' + str(i) for i in range(8)])) == [True] * 8
                final = snap()
                assert final['kids'][0]['wallet_cents'] == after['kids'][0]['wallet_cents'] + 9
                assert len(final['transactions']) == len(after['transactions']) + 9
                assert len(final['hosted_operations']) == len(after['hosted_operations']) + 9
                assert len(final['local_operations']) == len(after['local_operations']) + 9
                call('disable_allowance')
                disabled = read('kids', [])[0]
                assert disabled['allowance_enabled'] is False and disabled['next_allowance_ts'] is None
                print('PASS JS Worker -> Python DO: actions, serialization, denials, rollback, retries and concurrent calls')
            except BaseException:
                log.flush(); print((base / 'wrangler.log').read_text()); raise
            finally:
                stop(p)

if __name__ == '__main__': main()
