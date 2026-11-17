"""Disposable local Wrangler SQLite test. Never deploy the harness."""
import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from wrangler_helpers import isolated_environment, start_wrangler, stop

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
P = {'issuer':'https://id.example', 'subject':'alice'}
Q = {'issuer':'https://id.example', 'subject':'bob'}

def call_response(port, action, *args):
    req = urllib.request.Request(f'http://127.0.0.1:{port}/', data=json.dumps({'action':action, 'args':args}).encode(), headers={'Content-Type':'application/json'})
    try: response = urllib.request.urlopen(req, timeout=45)
    except urllib.error.HTTPError as e: response = e
    with response:
        return response.status, json.load(response)

def call(port, action, *args, ok=True):
    status, body = call_response(port, action, *args)
    assert (status == 200) == ok, (action, status, body)
    return body.get('result') if ok else body['error']

def main():
    with tempfile.TemporaryDirectory(prefix='bod-directory-') as temp:
        base = Path(temp); src = base / 'src'; src.mkdir()
        for f in ('bank.py', 'db.py', 'config.py'): shutil.copyfile(ROOT / f, src / f)
        shutil.copytree(ROOT / 'hosted', src / 'hosted', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(HERE / 'synthetic_directory.py', src / 'worker.py')
        (src / 'wrangler.jsonc').write_text(json.dumps({'name':'bod-directory-local-synthetic', 'main':'worker.py', 'compatibility_date':'2026-09-01', 'compatibility_flags':['python_workers','disable_python_external_sdk'], 'durable_objects':{'bindings':[{'name':'DIRECTORY','class_name':'SyntheticDirectory'}]}, 'migrations':[{'tag':'v1','new_sqlite_classes':['SyntheticDirectory']}]}))
        env = isolated_environment(base); p = None
        logpath = base / 'wrangler.log'
        with logpath.open('w+') as log:
            try:
                p, port = start_wrangler(src, base, log, env)
                f, a = call(port, 'create_family', P)
                g, b = call(port, 'create_family', Q)
                assert call(port, 'authorize', b, f, ok=False) == 'PermissionError'
                assert call(port, 'create_family', {}, ok=False) == 'ValueError'
                invite = call(port, 'invite', a, f, 'parent', 5)
                assert call(port, 'expire', Q, invite, ok=False) == 'PermissionError'
                assert call(port, 'rollback', ok=False) == 'RuntimeError'
                def attempt(_):
                    status, body = call_response(port, 'accept_invite', Q, invite)
                    if status == 200: return ('ok', body['result'])
                    assert status == 400 and body == {'error':'PermissionError'}, (status, body)
                    return ('denied', None)
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                    attempts = list(pool.map(attempt, range(8)))
                wins = [v for k,v in attempts if k == 'ok']; assert len(wins) == 1, attempts
                bob = wins[0]
                assert call(port, 'authorize', bob, f)['role'] == 'parent'
                assert {m['family'] for m in call(port, 'my_memberships', bob)} == {f, g}
                assert len(call(port, 'members', a, f)) == 2
                assert call(port, 'accept_invite', Q, invite, ok=False) == 'PermissionError'
                def revoke(_):
                    status, body = call_response(port, 'revoke', bob)
                    if status == 200:
                        assert body == {'result': True}, body
                        return True
                    assert status == 400 and body == {'error':'PermissionError'}, (status, body)
                    return False
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                    assert sum(pool.map(revoke, range(8))) == 1
                assert call(port, 'authorize', bob, f, ok=False) == 'PermissionError'
                stop(p); p = None
                p, port = start_wrangler(src, base, log, env)
                assert call(port, 'authorize', bob, f, ok=False) == 'PermissionError'
                assert call(port, 'accept_invite', Q, invite, ok=False) == 'PermissionError'
                assert len(call(port, 'members', a, f)) == 2
                assert call(port, 'authorize', b, g)['role'] == 'owner'
                print('PASS directory local DO: isolation, invalid input, expiry, rollback, concurrent single-use/revocation, restart')
            except BaseException:
                log.flush(); print(logpath.read_text(), file=sys.stderr); raise
            finally: stop(p)

if __name__ == '__main__': main()
