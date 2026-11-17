"""Disposable local Wrangler cross-DO RPC recovery smoke; no deployment."""
import concurrent.futures
import json
import shutil
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from wrangler_helpers import isolated_environment, start_wrangler, stop

ROOT = Path(__file__).resolve().parent.parent
F = 'a' * 32
OWNER = {'issuer': 'https://issuer.test', 'subject': 'owner'}
PARENT = {'issuer': 'https://issuer.test', 'subject': 'parent'}

def main():
    with tempfile.TemporaryDirectory(prefix='bod-recovery-') as temp:
        base = Path(temp); src = base / 'src'; src.mkdir()
        for file in ('bank.py', 'db.py', 'config.py'): shutil.copyfile(ROOT / file, src / file)
        shutil.copytree(ROOT / 'hosted', src / 'hosted', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(ROOT / 'tests_hosted/synthetic_recovery_worker.py', src / 'worker.py')
        (src / 'wrangler.jsonc').write_text(json.dumps({
            'name': 'bod-recovery-synthetic', 'main': 'worker.py',
            'compatibility_date': '2026-09-01', 'compatibility_flags': ['python_workers', 'disable_python_external_sdk'],
            'durable_objects': {'bindings': [{'name': 'RECOVERY', 'class_name': 'RecoveryDO'}, {'name': 'RELAY', 'class_name': 'RelayDO'}]},
            'migrations': [{'tag': 'v1', 'new_sqlite_classes': ['RecoveryDO', 'RelayDO']}]}))
        env = isolated_environment(base); process = None
        with (base / 'wrangler.log').open('w+') as log:
            try:
                process, port = start_wrangler(src, base, log, env)
                def call(action, *args):
                    query = urllib.parse.urlencode({'request': json.dumps({'action': action, 'args': args})})
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/?{query}', timeout=45) as r:
                        return json.load(r)
                def ok(action, *args):
                    result = call(action, *args)
                    assert 'result' in result, result
                    return result['result']
                ok('bootstrap_privileged', F, OWNER)
                owner = ok('issue_grant_privileged', F, OWNER, 100)
                ok('set_member', F, owner, PARENT, 'parent', 'active')
                parent = ok('issue_grant_privileged', F, PARENT, 100)
                assert ok('mutate', F, owner, 'seed', 'add_kid', ['Synthetic']) is True
                assert ok('acknowledge', F, owner, 'seed', True) is True
                assert ok('mutate', F, parent, 'lost', 'gift', [1, 9]) is True
                replacement = ok('issue_grant_privileged', F, PARENT, 100)
                ok('revoke', F, parent)
                assert call('pending', F, parent)['type'] == 'PermissionError'
                assert ok('pending', F, replacement)['operation_id'] == 'lost'
                assert call('mutate', F, replacement, 'blocked', 'gift', [1, 1])['type'] == 'ValueError'
                assert call('mutate', F, owner, 'lost', 'gift', [1, 9])['type'] == 'PermissionError'
                stop(process); process = None
                process, port = start_wrangler(src, base, log, env)
                assert ok('pending', F, replacement)['result'] is True
                assert ok('mutate', F, replacement, 'lost', 'gift', [1, 9]) is True
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    assert list(pool.map(lambda _: ok('acknowledge', F, replacement, 'lost', True), range(2))) == [True, True]
                before = ok('snapshot')
                assert call('fault', F, replacement, 'fault', 'gift', [1, 5])['type'] == 'RuntimeError'
                assert ok('snapshot') == before
                assert ok('pending', F, replacement) is None
                assert ok('read', F, owner, 'kids', [])[0]['wallet_cents'] == 509
                assert ok('mutate', F, replacement, 'fault', 'gift', [1, 5]) is True
                assert ok('read', F, owner, 'kids', [])[0]['wallet_cents'] == 514
                # Either serialized ordering is valid: a new action may run only
                # after the exact pending outcome's acknowledgment commits.
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    ack = pool.submit(call, 'acknowledge', F, replacement, 'fault', True)
                    next_op = pool.submit(call, 'mutate', F, replacement, 'next', 'gift', [1, 3])
                    assert ack.result()['result'] is True
                    outcome = next_op.result()
                if 'error' in outcome:
                    assert outcome['type'] == 'ValueError' and 'unacknowledged' in outcome['error'], outcome
                    assert ok('mutate', F, replacement, 'next', 'gift', [1, 3]) is True
                else:
                    assert outcome['result'] is True
                assert ok('pending', F, replacement)['operation_id'] == 'next'
                assert ok('read', F, owner, 'kids', [])[0]['wallet_cents'] == 517
                print('local cross-DO recovery, restart and rollback smoke passed')
            except BaseException:
                log.flush(); print((base / 'wrangler.log').read_text())
                raise
            finally:
                stop(process)

if __name__ == '__main__': main()
