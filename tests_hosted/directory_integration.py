"""Disposable local Wrangler SQLite test. Never deploy the harness."""
import concurrent.futures
import hashlib
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
                # Actual accept_invite SQL fault points, in the real DO transaction.
                for stage in ('fail_membership', 'fail_session'):
                    principal = {'issuer':'https://id.example', 'subject':stage}
                    token = call(port, 'invite', a, f, 'parent', 100)
                    before = call(port, 'snapshot')
                    assert call(port, stage, principal, token, ok=False) == 'InjectedFailure'
                    assert call(port, 'snapshot') == before, stage
                    redeemed = call(port, 'accept_invite', principal, token)
                    assert call(port, 'authorize', redeemed, f)['role'] == 'parent'

                token = call(port, 'invite', a, f, 'parent', 100)
                before = call(port, 'snapshot')
                principals = [{'issuer':'https://id.example', 'subject':f'racer-{i}'} for i in range(16)]
                def distinct_attempt(principal):
                    status, body = call_response(port, 'accept_invite', principal, token)
                    if status == 200: return (principal, body['result'])
                    assert status == 400 and body == {'error':'PermissionError'}, (status, body)
                    return None
                with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                    outcomes = list(pool.map(distinct_attempt, principals))
                winners = [outcome for outcome in outcomes if outcome is not None]
                assert len(winners) == 1, outcomes
                after = call(port, 'snapshot')
                assert len(after['memberships']) == len(before['memberships']) + 1
                assert len(after['sessions']) == len(before['sessions']) + 1
                added = [row for row in after['memberships'] if row not in before['memberships']]
                assert len(added) == 1 and added[0]['subject'] == winners[0][0]['subject']
                added_sessions = [row for row in after['sessions'] if row not in before['sessions']]
                assert len(added_sessions) == 1
                assert added_sessions[0]['digest'] == hashlib.sha256(winners[0][1].encode('ascii')).hexdigest()
                assert added_sessions[0]['subject'] == winners[0][0]['subject']
                assert all(row in after['memberships'] for row in before['memberships'])
                assert all(row in after['sessions'] for row in before['sessions'])
                assert call(port, 'authorize', winners[0][1], f)['role'] == 'parent'
                assert call(port, 'drop_invitations') is True
                assert call(port, 'authorize', a, f, ok=False) == 'RuntimeError'
                assert call(port, 'create_family', P, ok=False) == 'RuntimeError'
                print('PASS directory local DO: isolation, expiry, redemption fault rollback, distinct-principal single-use race, revocation, restart')
            except BaseException:
                log.flush(); print(logpath.read_text(), file=sys.stderr); raise
            finally: stop(p)

if __name__ == '__main__': main()
