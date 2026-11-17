"""Local disposable Wrangler/workerd integration; synthetic fixtures only."""
import concurrent.futures
import json
from pathlib import Path
import shutil
import sys
import tempfile
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / 'tests_hosted'))
from wrangler_helpers import isolated_environment, start_wrangler, stop


def run():
    with tempfile.TemporaryDirectory(prefix='revocation-') as tmp:
        base = Path(tmp); src = base / 'src'; src.mkdir()
        shutil.copyfile(HERE / 'worker.js', src / 'worker.js')
        (src / 'wrangler.jsonc').write_text(json.dumps({
            'name':'revocation-synthetic', 'main':'worker.js', 'compatibility_date':'2026-09-01',
            'durable_objects':{'bindings':[{'name':'COORD','class_name':'Coordinator'}, {'name':'FAMILY','class_name':'Family'}]},
            'migrations':[{'tag':'v1','new_sqlite_classes':['Coordinator','Family']}]}))
        env = isolated_environment(base); p = None
        with (base / 'log').open('w+') as log:
            try:
                p, port = start_wrangler(src, base, log, env)
                def call(to, message, family=None, valid=True, expected_status=None):
                    data=json.dumps({'to':to,'message':message,'family':family}).encode()
                    req=urllib.request.Request(f'http://127.0.0.1:{port}/', data=data, headers={'content-type':'application/json'})
                    try: r=urllib.request.urlopen(req, timeout=30)
                    except urllib.error.HTTPError as e: r=e
                    with r: status, body=r.status, json.load(r)
                    assert status == (expected_status if expected_status is not None else (200 if valid else 400)), (status,body,message)
                    return body
                def c(action, session='s', valid=True, **kw): return call('coordinator',dict(action=action,session=session,**kw),valid=valid)
                def f(family, action, session='s', valid=True, **kw): return call('family',dict(action=action,session=session,**({'family':family} if action in ('grant','fence') else {}),**kw),family,valid)
                def access(family, session='s', now=1): return f(family,'access',session,now=now)['ok']
                c('issue',expiry=100,now=0)
                ga=c('reserve',family='a',now=0)['grant']; gb=c('reserve',family='b',now=0)['grant']
                assert c('deliver',family='a',fault='lost')['unknown'] and access('a')
                assert c('deliver',family='not-reserved',fault='none',valid=False) == {'error':'unregistered'}
                assert c('deliver',family='a',fault='none')['ok']
                assert c('deliver',family='b',fault='none')['ok'] and access('b')
                before=c('status'); assert call('family',ga,'b',valid=False) == {'error':'wrong family'}
                assert c('status') == before
                assert access('b')
                c('begin'); assert c('reserve',family='c',now=1,valid=False) == {'error':'denied'}
                assert c('retry',family='a',fault='lost')['pending'] and not access('a') and access('b')
                assert c('retry',family='b',fault='rejected') == {'pending':True,'reason':'rpc failed'}
                assert access('b')
                assert c('retry',family='b',fault='stale') == {'pending':True,'reason':'stale ack'}
                assert not access('b')
                assert c('retry',family='b',fault='unreachable')['pending']
                s=c('status'); assert s['state']=='revoking' and [x['ack'] for x in s['targets']]==[0,0]
                assert not call('family',ga,'a')['ok']
                stop(p); p=None
                p, port = start_wrangler(src, base, log, env)
                assert c('status')['state']=='revoking' and not access('a')
                assert not c('retry',family='a',fault='none')['pending']
                assert c('status')['state']=='revoking'
                assert not c('retry',family='b',fault='none')['pending']
                assert c('status')['state']=='complete'
                assert not c('deliver',family='b',fault='none')['ok']
                assert not call('family',gb,'b')['ok'] and not access('b')
                assert not c('retry',family='b',fault='none')['pending']
                assert f('a','fence',gen=1)['ack']==1
                assert f('a','fence',gen=3)['ack']==3
                assert f('a','fence',gen=1)['ack']==1
                assert not call('family',ga,'a')['ok']
                c('begin'); assert c('status')['generation']==1
                assert c('issue',expiry=200,now=0,valid=False) == {'error':'session exists'} # never reuse
                c('issue',session='interleave',expiry=100,now=0)
                c('reserve',session='interleave',family='a',now=0)
                c('begin',session='interleave')
                assert c('deliver',session='interleave',family='a',fault='none')['ok']
                assert access('a','interleave') and c('status',session='interleave')['state']=='revoking'
                c('retry',session='interleave',family='a',fault='none')
                assert not access('a','interleave') and c('status',session='interleave')['state']=='complete'
                c('issue',session='expiry',expiry=10,now=0)
                ge=c('reserve',session='expiry',family='a',now=9)['grant']
                assert call('family',ge,'a')['ok']
                assert access('a','expiry',9) and not access('a','expiry',10)
                assert c('reserve',session='expiry',family='b',now=10,valid=False) == {'error':'denied'}
                c('begin',session='expiry'); c('retry',session='expiry',family='a',fault='none')
                assert c('issue',session='expiry',expiry=50,now=11,valid=False) == {'error':'session exists'}
                for bad, reason in (({'action':'grant','session':'x','family':'a','gen':-1,'expiry':10},'invalid number'), ({'action':'fence','session':'x','family':'a','gen':0},'bad fence'), ({'action':'access','session':'x','now':'1'},'invalid number'), ({'action':'nope','session':'x'},'unknown action')):
                    assert call('family',bad,'a',valid=False) == {'error':reason}
                assert c('retry',session='s',family='other',fault='none',valid=False) == {'error':'denied'}
                assert c('reserve',session='missing',family='a',now=0,valid=False) == {'error':'denied'}
                assert c('issue',session='bad/id',expiry=10,now=0,valid=False) == {'error':'invalid id'}
                # Concurrent real DO arrivals: every successful reservation must appear before completion.
                for i in range(12):
                    session=f'race{i}'; c('issue',session=session,expiry=100,now=0)
                    # Use status-aware raw requests: either winner is valid under either ordering.
                    def attempt():
                        data=json.dumps({'to':'coordinator','message':{'action':'reserve','session':session,'family':'racefamily','now':0}}).encode()
                        try:
                            with urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{port}/',data=data),timeout=30) as r: return json.load(r)
                        except urllib.error.HTTPError as e:
                            assert e.code==400 and json.load(e)=={'error':'denied'}; return None
                    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                        a=pool.submit(attempt); b=pool.submit(c,'begin',session); grant=a.result(); b.result()
                    state=c('status',session=session)
                    if grant:
                        assert state['state']=='revoking' and state['targets'][0]['ack']==0
                        c('retry',session=session,family='racefamily',fault='none')
                        assert not call('family',grant['grant'],'racefamily')['ok']
                    else: assert state['state']=='complete' and state['targets']==[]
                    assert c('status',session=session)['state']=='complete'
                # Direct arbitrary injection is deliberately unsafe even after zero-target completion.
                c('issue',session='unsafe',expiry=100,now=0); c('begin',session='unsafe')
                assert c('status',session='unsafe')['state']=='complete'
                assert call('family',{'action':'grant','family':'unregistered','session':'unsafe','gen':0,'expiry':100},'unregistered')['ok']
                assert access('unregistered','unsafe') and c('status',session='unsafe')['targets']==[]
                # Unhandled internal faults cannot masquerade as ordinary denial.
                assert call('family',{'action':'internal-error-test','session':'internal'},'a',expected_status=500)=={'error':'internal'}
                assert call('family',{'action':'grant','family':'a','session':'internal','gen':0,'expiry':10},'a')['ok']
                assert call('family',{'action':'grant','family':'a','session':'internal','gen':0,'expiry':11},'a',valid=False)=={'error':'conflicting grant'}
                assert call('coordinator',{'action':'issue','session':'internal-error','expiry':10,'now':0}, expected_status=200)=={'ok':True}
                # Concurrent grant/fence arrivals: either grant result is legal, fence always wins finally.
                for i in range(12):
                    session=f'grant-race{i}'; c('issue',session=session,expiry=100,now=0)
                    grant=c('reserve',session=session,family='racefamily',now=0)['grant']
                    c('begin',session=session)
                    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                        g=pool.submit(call,'family',grant,'racefamily')
                        fence=pool.submit(c,'retry',session,family='racefamily',fault='none')
                        assert g.result()['ok'] in (True,False)
                        assert fence.result()=={'pending':False}
                    assert not access('racefamily',session)
                    assert c('status',session=session)=={'state':'complete','generation':1,'targets':[{'family':'racefamily','ack':1}]}
                    assert not call('family',grant,'racefamily')['ok']
                # Competing retries may overlap the RPC; all converge on one durable ack.
                session='retry-race'; c('issue',session=session,expiry=100,now=0)
                c('reserve',session=session,family='retryfamily',now=0)
                c('begin',session=session)
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                    results=list(pool.map(lambda _: c('retry',session,family='retryfamily',fault='none'),range(8)))
                assert all(x=={'pending':False} for x in results)
                assert c('status',session=session)=={'state':'complete','generation':1,'targets':[{'family':'retryfamily','ack':1}]}
                print('PASS real SQLite Durable Objects and cross-DO RPC: fencing, lost/unreachable replies, restart, race, expiry, invalid input')
            except BaseException:
                log.flush(); print(Path(log.name).read_text(),file=sys.stderr); raise
            finally: stop(p)

if __name__=='__main__': run()
