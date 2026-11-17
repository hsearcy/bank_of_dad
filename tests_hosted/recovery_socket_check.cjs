// Disposable real WebSockets and fake identities; no production endpoint.
const assert = require('node:assert/strict'), fs = require('node:fs'), http = require('node:http');
const base=process.env.BASE_URL, checkpoint=process.env.CHECKPOINT, F='a'.repeat(32), G='b'.repeat(32);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function call(method,args=[],object='family',status=200) {
 const r=await fetch(base,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({method,args,object})});
 const b=await r.json(); assert.equal(r.status,status,`${method}: ${JSON.stringify(b)}`); return status===200?b.result:b.error;
}
async function socket(family,token,object='family') {
 const w=new WebSocket(`${base.replace('http:','ws:')}/socket?object=${object}&family=${family}&token=${token}`); w.frames=[];
 w.onmessage=e=>w.frames.push(e.data);
 await new Promise((ok,bad)=>{let timer=setTimeout(()=>bad(Error('socket timeout')),10000);w.onopen=()=>{clearTimeout(timer);ok()};w.onerror=()=>{clearTimeout(timer);bad(Error('socket denied'))}}); return w;
}
async function frames(w,n) {for(let i=0;i<30&&w.frames.length<n;i++) await sleep(20);await sleep(120);assert.equal(w.frames.length,n);assert.ok(w.frames.every(f=>f==='{"type":"invalidate"}'));}
(async()=>{
 if(process.env.RESTART) {
  const old=JSON.parse(fs.readFileSync(checkpoint)); const t=await call('grant',[F]); const s=await socket(F,t);
  assert.equal((await call('pending',[F,t])).operation_id,'lost'); assert.equal((await call('snapshot',[F,t])).revision,old.rev);
  assert.equal(await call('mutate',[F,t,'lost','gift',[1,5]]),true); await frames(s,0);
  assert.equal(await call('acknowledge',[F,t,'lost',true]),true); await frames(s,1);
  assert.equal(await call('acknowledge',[F,t,'lost',true]),true); await frames(s,1);
  s.close(); console.log('PASS recovery socket graceful restart and duplicate retries'); return;
 }
 const [a,a2,p]=await call('setup',[F]), [b]=await call('setup',[G],'other');
 const sa=await socket(F,a), sa2=await socket(F,a2), sp=await socket(F,p), sb=await socket(G,b,'other');
 assert.equal(await call('mutate',[G,b,'other-kid','add_kid',['Other']],'other'),true);
 await frames(sb,1); await Promise.all([frames(sa,0),frames(sa2,0),frames(sp,0)]);
 assert.equal(await call('acknowledge',[G,b,'other-kid',true],'other'),true); await frames(sb,2);
 await assert.rejects(socket(G,a,'other'));
 await assert.rejects(socket(G,a,'family'));
 for(const name of ['read','fixture_mutate','_mutate_in_transaction']) assert.match(await call('raw',[name,F,a,'x','gift',[1,1]],'family',400),/does not exist/);
 const counts=async(n)=>{await Promise.all([frames(sa,n),frames(sa2,n),frames(sp,n),frames(sb,2)])};
 assert.equal(await call('mutate',[F,a,'kid','add_kid',['Synthetic']]),true); await counts(1);
 assert.equal((await call('snapshot',[F,a])).revision,1);
 assert.equal(await call('acknowledge',[F,a,'kid',true]),true); await counts(2);
 assert.equal(await call('acknowledge',[F,a,'kid',true]),true); await counts(2);
 const before=await call('snapshot',[F,a]);
 let committedRead;
 sa.addEventListener('message',()=>{committedRead=call('pending',[F,a]);},{once:true});
 assert.deepEqual(await call('mutate',[F,a,'false','withdraw',[1,1]]),[false,'Nothing to take out']); await counts(3);
 assert.equal((await committedRead).operation_id,'false');
 assert.deepEqual(await call('snapshot',[F,a]),before); assert.equal((await call('pending',[F,a2])).operation_id,'false'); assert.equal(await call('pending',[F,p]),null);
 assert.equal(await call('outcome',[F,p,'false']),null); assert.equal((await call('outcome',[F,a,'false'])).result[0],false);
 await call('acknowledge',[F,p,'false',[false,'Nothing to take out']],'family',400);
 assert.deepEqual(await call('mutate',[F,a2,'false','withdraw',[1,1]]),[false,'Nothing to take out']); await counts(3);
 await call('acknowledge',[F,a,'false',[true,'Nothing to take out']],'family',400); await counts(3);
 assert.equal(await call('acknowledge',[F,a2,'false',[false,'Nothing to take out']]),true); await counts(4);
 assert.equal((await call('snapshot',[F,a])).revision,1);
 assert.equal(await call('mutate',[F,p,'noop','collect_rewards',[1,'invest']]),0); await counts(5);
 assert.equal((await call('snapshot',[F,a])).revision,1); assert.equal((await call('pending',[F,p])).operation_id,'noop'); assert.equal(await call('pending',[F,a]),null);
 assert.equal(await call('acknowledge',[F,p,'noop',0]),true); await counts(6);
 const snap=await call('snapshot',[F,a]);
 await call('fault',['mutate',[F,a,'fault','gift',[1,5]],'INSERT INTO local_recovery'],'family',400);
 assert.deepEqual(await call('snapshot',[F,a]),snap); assert.equal(await call('pending',[F,a]),null); await counts(6);
 assert.deepEqual(await Promise.all(Array.from({length:4},()=>call('mutate',[F,a,'action','gift',[1,2]]))),[true,true,true,true]); await counts(7);
 await call('fault',['acknowledge',[F,a,'action',true],'UPDATE local_recovery SET acknowledged=1'],'family',400);
 assert.equal((await call('pending',[F,a])).operation_id,'action'); await counts(7);
 assert.deepEqual(await Promise.all(Array.from({length:4},()=>call('acknowledge',[F,a,'action',true]))),[true,true,true,true]); await counts(8);
 await call('revoke',[F,a2]); await call('expire',[F,p]);
 assert.equal(await call('mutate',[F,a,'after','gift',[1,1]]),true); await frames(sa,9); await frames(sa2,8); await frames(sp,8); await frames(sb,2);
 await call('pending',[F,a2],'family',400); await call('pending',[F,p],'family',400);
 await assert.rejects(socket(F,a2)); await assert.rejects(socket(F,p));
 assert.equal(await call('acknowledge',[F,a,'after',true]),true); await frames(sa,10);
 // Close actual HTTP client after the gateway records the committed mutation.
 const u=new URL(base); const req=http.request(u,{method:'POST',headers:{'content-type':'application/json'}}); req.on('error',()=>{});
 req.end(JSON.stringify({method:'disconnect',args:[F,a,'lost','gift',[1,5]],object:'family'}));
 for(let i=0;i<200;i++){if(await call('gate')==='committed') break;await sleep(25);if(i===199)throw Error('no commit gate')}
 req.destroy(); assert.equal(await call('gate',['release']),'release');
 for(let i=0;i<200;i++){if(await call('gate')==='resumed') break;await sleep(25);if(i===199)throw Error('no response resume')}
 await frames(sa,11); assert.equal((await call('pending',[F,a])).operation_id,'lost');
 assert.equal(await call('mutate',[F,a,'lost','gift',[1,5]]),true); await frames(sa,11);
 fs.writeFileSync(checkpoint,JSON.stringify({rev:(await call('snapshot',[F,a])).revision}));
 [sa,sa2,sp,sb].forEach(w=>w.close()); console.log('PASS sockets, pending-only events, auth, rollback, lost response');
})().catch(e=>{console.error(e);process.exit(1)});
