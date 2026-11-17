"""Local JS Worker -> Python DO combined recovery/revision. Never deploy."""
import json, shutil, tempfile, urllib.error, urllib.request
from pathlib import Path
from wrangler_helpers import WRANGLER, isolated_environment, start_wrangler, stop
ROOT = Path(__file__).resolve().parent.parent
F = 'a' * 32
G = 'b' * 32

def main():
    with tempfile.TemporaryDirectory(prefix='bod-js-combined-') as tmp:
        base = Path(tmp); src = base / 'src'; src.mkdir()
        for name in ('bank.py','db.py','config.py'): shutil.copyfile(ROOT/name,src/name)
        shutil.copytree(ROOT/'hosted',src/'hosted',ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(ROOT/'tests_hosted/synthetic_js_combined.py',src/'worker.py')
        shutil.copyfile(ROOT/'tests_hosted/synthetic_js_combined_caller.js',src/'caller.js')
        (src/'wrangler.jsonc').write_text(json.dumps({'name':'synthetic-combined-bank','main':'worker.py','compatibility_date':'2026-09-01','compatibility_flags':['python_workers','disable_python_external_sdk'],'durable_objects':{'bindings':[{'name':'COMBINED','class_name':'SyntheticCombined'}]},'migrations':[{'tag':'v1','new_sqlite_classes':['SyntheticCombined']}]}))
        (src/'caller.jsonc').write_text(json.dumps({'name':'synthetic-combined-caller','main':'caller.js','compatibility_date':'2026-09-01','durable_objects':{'bindings':[{'name':'COMBINED','class_name':'SyntheticCombined','script_name':'synthetic-combined-bank'}]}}))
        p = None
        with (base/'wrangler.log').open('w+') as log:
            try:
                def start(): return start_wrangler(src,base,log,isolated_environment(base),command=[WRANGLER,'dev','-c',str(src/'caller.jsonc')])
                p,port = start()
                def call(method,args=(),obj='family',status=200):
                    req=urllib.request.Request(f'http://127.0.0.1:{port}/',data=json.dumps({'method':method,'args':args,'object':obj}).encode(),headers={'content-type':'application/json'})
                    try: r=urllib.request.urlopen(req,timeout=60)
                    except urllib.error.HTTPError as e: r=e
                    with r:
                        data=json.load(r); assert r.status==status,(method,r.status,data); return data.get('result') if status==200 else data['error']
                token,token2,parent=call('setup',[F]); other=call('setup',[G],obj='other')[0]
                def op(oid,action,args,expected,revision):
                    assert json.dumps(call('mutate',[F,token,oid,action,args])) == json.dumps(expected)
                    assert call('snapshot',[F,token])['revision'] == revision
                    committed=call('full')
                    tables=committed['tables']
                    assert tables['fixture_revision'] == [{'version':1,'revision':revision}]
                    for table in ('hosted_operations','local_operations','local_recovery'):
                        assert sum(row['operation_id']==oid for row in tables[table]) == 1, table
                    record=next(row for row in tables['local_recovery'] if row['operation_id']==oid)
                    assert (record['issuer'],record['subject'],record['acknowledged']) == ('https://issuer.test','owner',0)
                    assert json.dumps(json.loads(next(row for row in tables['hosted_operations'] if row['operation_id']==oid)['result'])) == json.dumps(expected)
                    assert json.dumps(call('mutate',[F,token2,oid,action,args])) == json.dumps(expected)
                    assert call('full') == committed
                    assert call('pending',[F,token2])['operation_id']==oid
                    assert call('acknowledge',[F,token2,oid,expected]) is True
                for n,(oid,action,args,result) in enumerate([('kid','add_kid',['Seed'],True),('gift','gift',[1,100],True),('save','save',[1,10],[True,'Saved!']),('withdraw','withdraw',[1,1],[True,'Done!']),('spend','spend',[1,1],[True,'Spent!']),('invest','invest',[1,'1mo',4],[True,'Locked in!'])],1):
                    op(oid,action,args,result,n)
                state=call('full')['tables']
                assert (state['kids'][0]['wallet_cents'],state['kids'][0]['savings_cents']) == (586,9)
                assert len(state['bonds']) == 1 and state['bonds'][0]['principal_cents'] == 4
                assert len(state['transactions']) == 5 and len(state['history']) == 5
                call('sql',['UPDATE bonds SET payout_cents=principal_cents, mature_ts=buy_ts WHERE id=1'])
                op('zero-change','collect_rewards',[1,'invest'],0,7)
                op('zero-noop','collect_rewards',[1,'invest'],0,7)
                op('drain','withdraw',[1,1000],[True,'Done!'],8)
                op('false','withdraw',[1,1],[False,'Nothing to take out'],8)
                wallet=call('snapshot',[F,token])['kids'][0]['wallet_cents']
                op('spend-all','spend',[1,wallet],[True,'Spent!'],9)
                for oid,action,args,result in [
                    ('false-save','save',[1,1],[False,'Too little to save']),
                    ('false-spend','spend',[1,1],[False,'No coins to spend']),
                    ('false-invest','invest',[1,'1mo',1],[False,'Need more coins first'])]:
                    op(oid,action,args,result,9)
                before=call('full')
                for prefix in ('UPDATE kids SET wallet_cents','INSERT INTO transactions','INSERT INTO history','INSERT INTO hosted_operations','INSERT INTO local_operations','INSERT INTO local_recovery','UPDATE fixture_revision SET revision=revision+1'):
                    for phase in ('before','after'):
                        assert call('fault',[[F,token,'fault','gift',[1,3]],prefix,phase],status=400)=='fault',(prefix,phase)
                        assert call('full') == before,(prefix,phase)
                # Discard the response after the actual committed transaction.
                assert call('drop',[F,token,'lost','gift',[1,3]]) is None
                committed=call('full')
                for phase in ('before','after'):
                    assert call('fault',[[F,token,'lost',True],'UPDATE local_recovery SET acknowledged=1',phase,'acknowledge'],status=400)=='fault'
                    assert call('full') == committed
                assert call('mutate',[F,token2,'new','gift',[1,1]],status=400)=='invalid'
                assert call('mutate',[F,token2,'lost','gift',[1,4]],status=400)=='invalid'
                assert call('pending',[F,parent]) is None
                assert call('outcome',[F,parent,'lost']) is None
                assert call('mutate',[F,parent,'lost','gift',[1,3]],status=400)=='denied'
                assert call('acknowledge',[F,token2,'lost',1],status=400)=='invalid'
                assert call('snapshot',[G,token],status=400)=='denied'
                assert call('snapshot',[F,other],obj='other',status=400)=='denied'
                assert call('snapshot',[G,other],obj='other')['revision']==0
                assert call('expire',[F,token],status=400)=='denied'
                assert call('raw',['_mutate_in_transaction',F,token,'raw','gift',[1,2]],status=400) in ('other','runtime')
                assert call('raw',['fixture_mutate',F,token,'raw','gift',[1,2]],status=400) in ('other','runtime')
                assert call('full') == committed
                stop(p); p=None
                p,port=start()
                assert call('pending',[F,token2])['operation_id']=='lost'
                assert call('mutate',[F,token2,'new','gift',[1,1]],status=400)=='invalid'
                assert call('mutate',[F,parent,'parent-op','gift',[1,2]]) is True
                assert call('pending',[F,parent])['operation_id']=='parent-op'
                assert call('pending',[F,token2])['operation_id']=='lost'
                assert call('acknowledge',[F,parent,'parent-op',True]) is True
                assert call('acknowledge',[F,token2,'lost',True]) is True
                assert call('mutate',[F,token2,'new','gift',[1,1]]) is True
                call('revoke',[F,token2])
                assert call('pending',[F,token2],status=400)=='denied'
                assert call('mutate',[F,token2,'new','gift',[1,1]],status=400)=='denied'
                for version in (1,2):
                    obj='legacy'+str(version); old=call('setup',[F,version],obj=obj)[0]
                    before=call('full',obj=obj)
                    assert call('snapshot',[F,old],obj=obj,status=400)=='runtime'
                    assert call('full',obj=obj)==before
                broken='broken'; t=call('setup',[F],obj=broken)[0]
                call('sql',['DROP INDEX local_recovery_pending'],obj=broken)
                before=call('full',obj=broken)
                assert call('snapshot',[F,t],obj=broken,status=400)=='runtime'
                assert call('full',obj=broken)==before
                print('PASS local JS -> Python combined recovery/revision; restart, authorization, fault rollback, legacy rejection')
            except BaseException:
                log.flush(); print((base/'wrangler.log').read_text()); raise
            finally: stop(p)
if __name__=='__main__': main()
