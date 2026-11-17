// Real local WebSockets and JS -> Python RPC, synthetic identities only.
const assert=require('node:assert/strict'), fs=require('node:fs');
const base=process.env.BASE_URL, internal=process.env.DRIVER_URL, F='a'.repeat(32), G='b'.repeat(32), secret=process.env.TEST_SECRET;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const stableState=({server_now, ...rest})=>rest;
async function driver(family,method,args=[],status=200) {
 const r=await fetch(internal+'/__unsafe_test_driver/call',{method:'POST',headers:{'content-type':'application/json','x-test-secret':secret},body:JSON.stringify({family,method,args})});
 const body=await r.json(); assert.equal(r.status,status,`${method}: ${JSON.stringify(body)}`); return body;
}
async function get(family,token,path,status=200) {
 const r=await fetch(base+path,{headers:{'x-spike-family':family,'x-spike-token':token}}); assert.equal(r.status,status,`${path}: ${await r.clone().text()}`); return status===200?r.json():null;
}
async function socket(family,token) {
 const w=new WebSocket(`${base.replace('http:','ws:')}/__spike/socket?family=${family}&token=${token}`); w.frames=[]; w.onmessage=e=>w.frames.push(e.data);
 await new Promise((ok,bad)=>{const t=setTimeout(()=>bad(Error('socket timeout')),10000);w.onopen=()=>{clearTimeout(t);ok()};w.onerror=()=>{clearTimeout(t);bad(Error('socket denied'))}}); return w;
}
async function frames(w,n) {for(let i=0;i<50 && w.frames.length<n;i++) await sleep(20); await sleep(100); assert.equal(w.frames.length,n); assert.ok(w.frames.every(x=>x==='{"type":"invalidate"}'));}
(async()=>{
 if(process.env.RESTART) {
  const old=JSON.parse(fs.readFileSync(process.env.CHECKPOINT)); const a=await driver(F,'grant',[F]);
  assert.deepEqual(stableState(await get(F,a,'/api/state')),stableState(old.state)); assert.equal((await get(F,a,'/api/pending')).operation_id,'retry');
  const s=await socket(F,a), before=await driver(F,'full_snapshot');
  assert.equal(await driver(F,'mutate',[F,a,'retry','gift',[1,7]]),true); assert.deepEqual(await driver(F,'full_snapshot'),before); await frames(s,0);
  assert.equal(await driver(F,'acknowledge',[F,a,'retry',true]),true); await frames(s,1); assert.equal(await get(F,a,'/api/pending'),null); s.close(); console.log('PASS pending dashboard graceful restart and replay'); return;
 }
 const [a,a2,p]=await driver(F,'setup',[F]), [b]=await driver(G,'setup',[G]);
 const [sa,sa2,sp,sb]=await Promise.all([socket(F,a),socket(F,a2),socket(F,p),socket(G,b)]);
 assert.equal(await driver(G,'mutate',[G,b,'other','add_kid',['Other']]),true);
 assert.equal(await driver(G,'acknowledge',[G,b,'other',true]),true);
 await Promise.all([frames(sb,2),frames(sa,0),frames(sa2,0),frames(sp,0)]);
 await get(G,a,'/api/state',403); await get(G,a,'/api/pending',403);
 assert.equal((await fetch(`${base}/__spike/socket?family=${G}&token=${a}`)).status,403);
 const state=await get(F,a,'/api/state'); assert.deepEqual(state.kids,[]); assert.equal(state.revision,0); assert.ok(state.config.bonds.length); assert.equal(typeof state.server_now,'number');
 const paths=[...JSON.parse(process.env.POST_ROUTES),'/api/state','/api/transactions','/api/pending','/api/unknown','/rpc','/__spike/socket','/__unsafe_test_driver/call','/__unsafe_test_driver/','/__test/setup','/__spike/setup'];
 for(const path of paths) for (const method of ['POST','PUT','PATCH','DELETE','OPTIONS','HEAD']) {
   const beforeF=await driver(F,'full_snapshot'), beforeG=await driver(G,'full_snapshot');
   const options={method,headers:{'x-spike-family':F,'x-spike-token':a,'x-test-secret':secret,'content-type':'application/json'}};
   if (method !== 'HEAD') options.body='{}';
   const r=await fetch(base+path.replace(/<[^>]+>/g,'1'),options);
   assert.equal(r.status,405,`${method} ${path}`);
   assert.deepEqual(await driver(F,'full_snapshot'),beforeF); assert.deepEqual(await driver(G,'full_snapshot'),beforeG);
 }
 for (const path of ['/__unsafe_test_driver/call','/__unsafe_test_driver/','/__test/setup','/__spike/setup']) {
   assert.equal((await fetch(base+path,{headers:{'x-test-secret':secret}})).status,404,path);
 }
 assert.equal((await fetch(internal+'/__unsafe_test_driver/call',{method:'POST'})).status,403);
 const wrongF=await driver(F,'full_snapshot'), wrongG=await driver(G,'full_snapshot');
 assert.deepEqual(await driver(G,'mutate',[G,a,'wrong','gift',[1,7]],400),{error:'denied'});
 assert.deepEqual(await driver(F,'full_snapshot'),wrongF); assert.deepEqual(await driver(G,'full_snapshot'),wrongG);
 assert.equal(await driver(F,'mutate',[F,a,'kid','add_kid',['Synthetic']]),true);
 await Promise.all([frames(sa,1),frames(sa2,1),frames(sp,1),frames(sb,2)]);
 assert.equal((await get(F,a,'/api/state')).revision,1); assert.equal((await get(F,a,'/api/pending')).operation_id,'kid');
 const tx=await get(F,a,'/api/transactions?kid_id=1'); assert.ok(Array.isArray(tx.transactions)); assert.equal(tx.revision,1);
 assert.equal(await driver(F,'acknowledge',[F,a,'kid',true]),true); await Promise.all([frames(sa,2),frames(sa2,2),frames(sp,2)]);
 const before=await get(F,a,'/api/state');
 assert.deepEqual(await driver(F,'mutate',[F,a,'false','withdraw',[1,1]]),[false,'Nothing to take out']);
 await Promise.all([frames(sa,3),frames(sa2,3),frames(sp,3),frames(sb,2)]);
 assert.deepEqual(stableState(await get(F,a,'/api/state')),stableState(before)); assert.equal((await get(F,a2,'/api/pending')).operation_id,'false'); assert.equal(await get(F,p,'/api/pending'),null);
 const db=await driver(F,'full_snapshot');
 assert.deepEqual(await driver(F,'mutate',[F,a,'blocked','gift',[1,1]],400),{error:'pending'});
 assert.deepEqual(await driver(F,'acknowledge',[F,a,'false',[true,'Nothing to take out']],400),{error:'result_mismatch'});
 const unrelated=await driver(F,'fault',['bogus',[], ''],400); assert.deepEqual(unrelated,{error:'other'});
 assert.throws(()=>assert.deepEqual(unrelated,{error:'pending'})); assert.deepEqual(await driver(F,'full_snapshot'),db);
 assert.deepEqual(await driver(F,'mutate',[F,a2,'false','withdraw',[1,1]]),[false,'Nothing to take out']); await frames(sa,3);
 assert.equal(await driver(F,'acknowledge',[F,a2,'false',[false,'Nothing to take out']]),true); await Promise.all([frames(sa,4),frames(sa2,4),frames(sp,4)]); assert.equal(await get(F,a,'/api/pending'),null);
 const pre=await driver(F,'full_snapshot');
 assert.deepEqual(await driver(F,'fault',['mutate',[F,a,'retry','gift',[1,7]],'INSERT INTO local_recovery'],400),{error:'injected_fault'}); assert.deepEqual(await driver(F,'full_snapshot'),pre); await frames(sa,4);
 assert.equal(await driver(F,'mutate',[F,a,'retry','gift',[1,7]]),true); await Promise.all([frames(sa,5),frames(sa2,5),frames(sp,5),frames(sb,2)]);
 const after=await get(F,a,'/api/state'); assert.equal(after.revision,before.revision+1); assert.equal((await get(F,p,'/api/pending')),null);
 const post=await driver(F,'full_snapshot');
 assert.deepEqual(pre.tables.kids[0].wallet_cents,{type:'integer',value:500}); assert.deepEqual(post.tables.kids[0].wallet_cents,{type:'integer',value:507});
 assert.deepEqual(pre.tables.transactions,[]); assert.equal(post.tables.transactions.length,1);
 const row=post.tables.transactions[0];
 assert.deepEqual(row.id,{type:'integer',value:1}); assert.deepEqual(row.kid_id,{type:'integer',value:1});
 assert.deepEqual(row.kind,{type:'text',value:'gift'}); assert.deepEqual(row.amount_cents,{type:'integer',value:7});
 assert.deepEqual(row.note,{type:'text',value:'From Dad'}); assert.deepEqual(row.bond_id,{type:'null',value:null});
 assert.equal(row.ts.type,'real'); assert.equal(typeof row.ts.value,'number');
 assert.ok(Array.isArray(after.kids)); assert.equal(after.kids.length,1); assert.equal(after.kids[0].wallet_cents,507); assert.equal(typeof after.kids[0].wallet_cents,'number');
 const txAfter=await get(F,a,'/api/transactions?kid_id=1'); assert.ok(Array.isArray(txAfter.transactions)); assert.equal(txAfter.transactions.length,1);
 assert.equal(txAfter.transactions[0].kind,'gift'); assert.equal(txAfter.transactions[0].amount_cents,7); assert.equal(typeof txAfter.transactions[0].amount_cents,'number');
 const pending=await get(F,a,'/api/pending'); assert.equal(pending.operation_id,'retry'); assert.equal(pending.result,true);
 fs.writeFileSync(process.env.CHECKPOINT,JSON.stringify({state:after})); [sa,sa2,sp,sb].forEach(s=>s.close()); console.log('PASS read-only adapter, pending, socket, isolation, rollback and retry');
})().catch(e=>{console.error(e);process.exit(1)});
