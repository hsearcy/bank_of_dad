"""Synthetic-only local DO authorization integration."""
import concurrent.futures
import json
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from wrangler_helpers import isolated_environment, start_wrangler, stop

ROOT = Path(__file__).resolve().parent.parent
F = 'a'*32
O = {'issuer':'https://issuer.test', 'subject':'owner'}
P = {'issuer':'https://issuer.test', 'subject':'parent'}

def main():
    with tempfile.TemporaryDirectory(prefix='bod-auth-') as temp:
        base = Path(temp); src = base / 'src'; src.mkdir()
        for f in ('bank.py','db.py','config.py'): shutil.copyfile(ROOT/f, src/f)
        shutil.copytree(ROOT/'hosted', src/'hosted', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(ROOT/'tests_hosted/synthetic_auth_worker.py', src/'worker.py')
        (src/'wrangler.jsonc').write_text(json.dumps({'name':'bod-auth-synthetic','main':'worker.py','compatibility_date':'2026-09-01','compatibility_flags':['python_workers','disable_python_external_sdk'],'durable_objects':{'bindings':[{'name':'AUTH','class_name':'SyntheticAuth'}]},'migrations':[{'tag':'v1','new_sqlite_classes':['SyntheticAuth']}]}))
        env = isolated_environment(base); p = None
        with (base/'wrangler.log').open('w+') as log:
            try:
                p, port = start_wrangler(src, base, log, env)
                def call(action, args, now=1000, status=200, object_name="one"):
                    q = urllib.parse.urlencode({'action':action,'args':json.dumps(args),'now':now,'object':object_name})
                    try: r = urllib.request.urlopen(f'http://127.0.0.1:{port}/?{q}',timeout=45)
                    except urllib.error.HTTPError as e: r=e
                    with r:
                        body=json.load(r); assert r.status == status, (action,body); return body
                call('bootstrap_privileged',[F,O])
                family2 = 'b'*32
                call('bootstrap_privileged',[family2,O],object_name='two')
                owner2 = call('issue_grant_privileged',[family2,O,100],object_name='two')['result']
                call('mutate',[family2,owner2,'kid','add_kid',['Other family']],object_name='two')
                assert call('read',[family2,owner2,'kids',[]],object_name='two')['result'][0]['name']=='Other family'

                owner=call('issue_grant_privileged',[F,O,100])['result']
                assert call('read',[family2,owner,'kids',[]],status=500,object_name='two')['type']=='PermissionError'
                assert call('read',[F,owner2,'kids',[]],status=500)['type']=='PermissionError'
                call('set_member',[F,owner,P,'parent','active'])
                parent=call('issue_grant_privileged',[F,P,100])['result']
                call('mutate',[F,owner,'kid','add_kid',['Test']])
                call('mutate',[F,parent,'first','gift',[1,3]])
                mutations = {'add_kid':['Extra'], 'gift':[1,20], 'save':[1,5],
                             'withdraw':[1,1], 'spend':[1,1], 'invest':[1,'1mo',1],
                             'collect_rewards':[1]}
                for role, token in [('owner',owner), ('parent',parent)]:
                    for action, args in mutations.items():
                        call('mutate',[F,token,role+'-'+action,action,args])
                    for action, args in [('kids',[]), ('transactions',[1])]:
                        call('read',[F,token,action,args])
                replacement = call('issue_grant_privileged',[F,P,100])['result']
                before_retry = call('snapshot',[])['result']
                call('mutate',[F,replacement,'parent-gift','gift',[1,20]])
                assert call('snapshot',[])['result']==before_retry
                for action, args in mutations.items():
                    assert call('mutate',[F,'invalid','invalid-'+action,action,args],status=500)['type']=='PermissionError'
                for action, args in [('kids',[]), ('transactions',[1])]:
                    assert call('read',[F,'invalid',action,args],status=500)['type']=='PermissionError'
                assert call('mutate',[F,owner,'first','gift',[1,3]],status=500)['type']=='PermissionError'
                before=call('read',[F,owner,'kids',[]])['result']
                snapshot = call('snapshot',[])['result']
                assert 'after bank write' in call('fault',[F,owner,'rollback','gift',[1,9]],status=500)['error']
                assert call('snapshot',[])['result']==snapshot
                assert call('read',[F,owner,'kids',[]])['result']==before
                assert call('mutate',[F,owner,'rollback','gift',[1,9]])['result'] is True
                assert call('mutate',[F,owner,'rollback','gift',[1,9]])['result'] is True
                before=call('read',[F,owner,'kids',[]])['result']
                call('revoke',[F,parent])
                assert call('mutate',[F,parent,'revoked','gift',[1,3]],status=500)['type']=='PermissionError'
                assert call('read',['b'*32,owner,'kids',[]],status=500)['type']=='PermissionError'
                assert call('read',[F,owner,'kids',[]],now=1100,status=500)['type']=='PermissionError'
                active = call('issue_grant_privileged',[F,P,100])['result']
                assert call('set_member',[F,active,P,'parent','active'],status=500)['type']=='PermissionError'
                call('set_member',[F,owner,P,'parent','disabled'])
                assert call('mutate',[F,active,'disabled','gift',[1,1]],status=500)['type']=='PermissionError'
                call('set_member',[F,owner,P,'parent','active'])
                assert call('read',[F,active,'kids',[]],status=500)['type']=='PermissionError'
                racing = call('issue_grant_privileged',[F,P,100])['result']
                def race_mutation():
                    q = urllib.parse.urlencode({'action':'mutate','args':json.dumps([F,racing,'race','gift',[1,13]]),'now':1000})
                    try: r=urllib.request.urlopen(f'http://127.0.0.1:{port}/?{q}',timeout=45)
                    except urllib.error.HTTPError as e: r=e
                    with r: return r.status, json.load(r)
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    operation = pool.submit(race_mutation)
                    revocation = pool.submit(call, 'revoke', [F,racing])
                    code, outcome = operation.result(); revocation.result()
                assert code == 200 or (code == 500 and outcome['type'] == 'PermissionError')
                raced = call('read',[F,owner,'kids',[]])['result']
                assert raced[0]['wallet_cents'] == before[0]['wallet_cents'] + (13 if code == 200 else 0)
                assert call('mutate',[F,racing,'race','gift',[1,13]],status=500)['type']=='PermissionError'
                before = raced
                def retry(_): return call('mutate',[F,owner,'concurrent','gift',[1,7]])['result']
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool: assert all(pool.map(retry,range(8)))
                state=call('read',[F,owner,'kids',[]])['result']
                assert state[0]['wallet_cents']==before[0]['wallet_cents']+7
                stop(p); p=None; p,port=start_wrangler(src,base,log,env)
                assert call('read',[F,owner,'kids',[]])['result']==state
                assert call('read',[family2,owner2,'kids',[]],object_name='two')['result'][0]['name']=='Other family'
                assert call('read',[family2,owner,'kids',[]],status=500,object_name='two')['type']=='PermissionError'
                assert call('read',[F,owner2,'kids',[]],status=500)['type']=='PermissionError'
                assert call('mutate',[F,owner,'concurrent','gift',[1,7]])['result'] is True
                assert call('read',[F,parent,'kids',[]],status=500)['type']=='PermissionError'
                print('PASS synthetic DO authorization, rollback, concurrent races, family isolation, restart')
            except BaseException:
                log.flush(); print((base/'wrangler.log').read_text(),file=sys.stderr); raise
            finally: stop(p)

if __name__=='__main__': main()
