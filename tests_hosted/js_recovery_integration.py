"""Local synthetic JS Worker -> Python recovery DO native JSON RPC. NEVER deploy."""
import json, shutil, tempfile, urllib.error, urllib.request
from pathlib import Path
from wrangler_helpers import WRANGLER, isolated_environment, start_wrangler, stop
ROOT=Path(__file__).resolve().parent.parent
F='a'*32

def main():
    with tempfile.TemporaryDirectory(prefix='bod-js-recovery-') as tmp:
        base=Path(tmp); src=base/'src'; src.mkdir()
        for name in ('bank.py','db.py','config.py'): shutil.copyfile(ROOT/name,src/name)
        shutil.copytree(ROOT/'hosted',src/'hosted',ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(ROOT/'tests_hosted/synthetic_js_recovery.py',src/'worker.py')
        shutil.copyfile(ROOT/'tests_hosted/synthetic_js_recovery_caller.js',src/'caller.js')
        (src/'wrangler.jsonc').write_text(json.dumps({'name':'synthetic-recovery-bank','main':'worker.py','compatibility_date':'2026-09-01','compatibility_flags':['python_workers','disable_python_external_sdk'],'durable_objects':{'bindings':[{'name':'RECOVERY','class_name':'SyntheticRecovery'}]},'migrations':[{'tag':'v1','new_sqlite_classes':['SyntheticRecovery']}]}))
        (src/'caller.jsonc').write_text(json.dumps({'name':'synthetic-recovery-caller','main':'caller.js','compatibility_date':'2026-09-01','durable_objects':{'bindings':[{'name':'RECOVERY','class_name':'SyntheticRecovery','script_name':'synthetic-recovery-bank'}]}}))
        p=None
        with (base/'wrangler.log').open('w+') as log:
            try:
                p,port=start_wrangler(src,base,log,isolated_environment(base),command=[WRANGLER,'dev','-c',str(src/'caller.jsonc')])
                def call(method,args=(),obj='family',status=200):
                    req=urllib.request.Request(f'http://127.0.0.1:{port}/',data=json.dumps({'method':method,'args':args,'object':obj}).encode(),headers={'content-type':'application/json'})
                    try: r=urllib.request.urlopen(req,timeout=60)
                    except urllib.error.HTTPError as e: r=e
                    with r:
                        data=json.load(r); assert r.status==status,(method,r.status,data); return data
                token=call('setup')['result']
                assert call('pending',[F,token])=={'result':None}
                assert call('mutate',[F,token,'seed','add_kid',['Zoë 🐉']])=={'result':True}
                expected={'operation_id':'seed','request':['add_kid',['Zoë 🐉','🐣','#4f9dde']],'result':True,'acknowledged':False}
                assert call('pending',[F,token])=={'result':expected}
                assert call('acknowledge',[F,token,'seed',1],status=400)=={'error':'invalid'}
                assert call('mutate',[F,token,'blocked','gift',[1,1]],status=400)=={'error':'invalid'}
                assert call('acknowledge',[F,token,'seed',True])=={'result':True}
                assert call('mutate',[F,token,'seed','add_kid',['Zoë 🐉','🐣','#4f9dde']])=={'result':True}
                assert call('outcome',[F,token,'seed'])['result']==dict(expected,acknowledged=True)
                assert call('read',[F,token,'kids',[]])['result'][0]['name']=='Zoë 🐉'
                assert call('mutate',[F,token,'zero','collect_rewards',[1,'allowance']])=={'result':0}
                assert call('acknowledge',[F,token,'zero',False],status=400)=={'error':'invalid'}
                assert call('acknowledge',[F,token,'zero',0])=={'result':True}
                assert call('mutate',[F,token,'spend-all','spend',[1,500]])=={'result':[True,'Spent!']}
                assert call('acknowledge',[F,token,'spend-all',[True,'Spent!']])=={'result':True}
                failure=[False,'No coins to spend']
                assert call('mutate',[F,token,'failure','spend',[1,1]])=={'result':failure}
                assert call('pending',[F,token])['result']['result']==failure
                assert call('acknowledge',[F,token,'failure',[False,'wrong']],status=400)=={'error':'invalid'}
                assert call('acknowledge',[F,token,'failure',failure])=={'result':True}
                args=[F,token,'fault','gift',[1,7]]
                assert call('fault',[args,'INSERT INTO local_recovery'],status=400)=={'error':'fault'}
                assert call('pending',[F,token])=={'result':None}
                assert call('read',[F,token,'kids',[]])['result'][0]['wallet_cents']==0
                assert call('mutate',args)=={'result':True}
                assert call('pending',[F,'invalid'],status=400)=={'error':'denied'}
                # Invoke actual v1 FamilyBank.read against v2 storage, then vice versa.
                assert call('v1_read',[token],status=400)=={'error':'runtime'}
                legacy=call('setup',[True],obj='legacy')['result']
                assert call('v1_read',[legacy],obj='legacy')=={'result':[]}
                assert call('pending',[F,legacy],obj='legacy',status=400)=={'error':'runtime'}
                print('PASS local JS Worker -> Python recovery DO native JSON, denials, rollback and v1/v2 rejection')
            except BaseException:
                log.flush(); print((base/'wrangler.log').read_text()); raise
            finally: stop(p)
if __name__=='__main__': main()
