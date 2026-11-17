// Synthetic local runtime checks: no production data or services.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = process.env.BASE_URL, A='a'.repeat(32), B='b'.repeat(32);
const sleep = ms => new Promise(r=>setTimeout(r,ms));
async function call(action, args=[], object=A, status=200) {
  const r=await fetch(`${base}/${action}?object=${object}`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(args)});
  const body=await r.json(); assert.equal(r.status,status,`${action}: ${JSON.stringify(body)}`); return status === 200 ? body.result : body.error;
}
async function socket(family, token, object=family) {
  const ws=new WebSocket(`${base.replace('http:','ws:')}/socket?object=${object}&family=${family}&token=${token}`);
  ws.messages=[]; ws.addEventListener('message',e=>ws.messages.push(JSON.parse(e.data)));
  await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;setTimeout(()=>reject(Error('socket timeout')),5000)});
  return ws;
}
(async()=>{
 if (process.env.RESTART_CHECK) {
   const saved=JSON.parse(fs.readFileSync(process.env.CHECKPOINT,'utf8'));
   const state=await call('fixture_dashboard',[B,saved.token],B);
   assert.equal(state.revision,saved.revision); assert.equal(state.kids[0].wallet_cents,saved.wallet);
   assert.deepEqual(await call('fixture_snapshot',[],B),saved.full);
   assert.deepEqual(await call('fixture_snapshot',[],'c'.repeat(32)),saved.fullC);
   assert.equal(await call('fixture_mutate',[B,saved.token,'restart-gift','gift',[1,7]],B),true);
   const next=await call('fixture_dashboard',[B,saved.token],B);
   assert.equal(next.revision,saved.revision+1); assert.equal(next.kids[0].wallet_cents,saved.wallet+7);
   console.log('PASS graceful local runtime restart retains bank state and revision'); return;
 }
 const empty=await call('fixture_snapshot',[], 'fresh');
 await call('fixture_bootstrap_fault',[A],'fresh',403);
 assert.deepEqual(await call('fixture_snapshot',[],'fresh'),empty);
 const [fresh]=await call('fixture_setup',[A],'fresh');
 assert.equal((await call('fixture_dashboard',[A,fresh],'fresh')).revision,1);
 await call('fixture_break_schema',[],'fresh');
 const altered=await call('fixture_snapshot',[],'fresh');
 await call('fixture_dashboard',[A,fresh],'fresh',403);
 await call('fixture_mutate',[A,fresh,'bad','gift',[1,1]],'fresh',403);
 assert.deepEqual(await call('fixture_snapshot',[],'fresh'),altered);
 const [a,a2]=await call('fixture_setup',[A]); const [b]=await call('fixture_setup',[B],B);
 let state=await call('fixture_dashboard',[A,a]); assert.equal(state.revision,1); assert.equal(state.kids[0].wallet_cents,500);
 assert.equal((await call('fixture_dashboard',[B,b],B)).revision,1);
 fs.writeFileSync(process.env.CHECKPOINT, JSON.stringify({token:b,revision:1,wallet:500,full:await call('fixture_snapshot',[],B)}));
 const sa=await socket(A,a), sa2=await socket(A,a2), sb=await socket(B,b);
 await assert.rejects(socket(B,a,B));
 let observed;
 sa.addEventListener('message', e=>{if (JSON.parse(e.data).revision===2) observed=call('fixture_dashboard',[A,a]);});
 const full=()=>call('fixture_snapshot',[]);
 const before=await full();
 await call('mutate',[A,a,'bypass','gift',[1,99]],A,403); assert.deepEqual(await full(),before);
 await call('fixture_mutate',[B,a,'wrong','gift',[1,99]],B,403);
 for (const stage of ['before_revision','after_revision']) {
   assert.match(await call('fixture_fault',[A,a,'fault','gift',[1,99],stage],A,403), /synthetic fault/); assert.deepEqual(await full(),before);
 }
 await sleep(150); assert.deepEqual(sa.messages,[]); assert.deepEqual(sb.messages,[]);
 assert.equal(await call('fixture_mutate',[A,a,'gift','gift',[1,100]]),true);
 await sleep(150); assert.deepEqual(sa.messages,[{type:'invalidate',revision:2}]); assert.deepEqual(sa2.messages,sa.messages); assert.deepEqual(sb.messages,[]);
 assert.ok(observed); const postCommit=await observed; assert.equal(postCommit.revision,2); assert.equal(postCommit.kids[0].wallet_cents,600);
 state=await call('fixture_dashboard',[A,a]); assert.equal(state.revision,2); assert.equal(state.kids[0].wallet_cents,600);
 const after=await full(); assert.equal(await call('fixture_mutate',[A,a,'gift','gift',[1,100]]),true); assert.deepEqual(await full(),after);
 await call('fixture_mutate',[A,a,'gift','gift',[1,101]],A,403); assert.deepEqual(await full(),after);
 assert.deepEqual(await call('fixture_mutate',[A,a,'empty','spend',[1,600]]),[true,'Spent!']);
 assert.deepEqual(await call('fixture_mutate',[A,a,'fail','save',[1,1]]),[false,'Too little to save']);
 await sleep(150); assert.deepEqual(sa.messages,[{type:'invalidate',revision:2},{type:'invalidate',revision:3}]); assert.deepEqual(sb.messages,[]);
 assert.equal((await call('fixture_dashboard',[A,a])).revision,3);
 await call('fixture_revoke',[A,a2]);
 await call('fixture_mutate',[A,a,'next','gift',[1,5]]);
 await sleep(150); assert.equal(sa.messages.at(-1).revision,4); assert.deepEqual(sa2.messages.map(x=>x.revision),[2,3]); assert.deepEqual(sb.messages,[]);
 await call('fixture_dashboard',[A,a2],A,403);
 const calls=Array.from({length:5},()=>call('fixture_mutate',[A,a,'race','gift',[1,7]])); await Promise.all(calls);
 await sleep(150); assert.equal(sa.messages.filter(x=>x.revision===5).length,1);
 state=await call('fixture_dashboard',[A,a]); assert.equal(state.revision,5); assert.equal(state.kids[0].wallet_cents,12);
 await Promise.all(Array.from({length:4},(_,i)=>call('fixture_mutate',[A,a,'unique-'+i,'gift',[1,1]])));
 await sleep(150);
 assert.deepEqual(sa.messages.slice(-4).map(x=>x.revision),[6,7,8,9]);
 state=await call('fixture_dashboard',[A,a]); assert.equal(state.revision,9); assert.equal(state.kids[0].wallet_cents,16);
 assert.deepEqual(sb.messages,[]);
 const mixed=[];
 for(let i=0;i<6;i++) {
   mixed.push(call('fixture_mutate',[A,a,'mixed-'+i,'gift',[1,1]]));
   mixed.push(call('fixture_dashboard',[A,a]).then(s=>{assert.equal(s.kids[0].wallet_cents,s.revision+7)}));
 }
 await Promise.all(mixed);
 state=await call('fixture_dashboard',[A,a]); assert.equal(state.revision,15); assert.equal(state.kids[0].wallet_cents,22);
 // A separate family covers every action without disturbing socket checks.
 const C='c'.repeat(32), [c]=await call('fixture_setup',[C],C);
 const cf=()=>call('fixture_snapshot',[],C), cd=()=>call('fixture_dashboard',[C,c],C);
 const sc=await socket(C,c); let rev=1;
 const domain=['kids','bonds','transactions','history'];
 function bookkeeping(old, next, id, result) {
   for (const table of ['hosted_operations','local_operations']) {
     const added=next.tables[table].filter(x=>x.operation_id===id);
     assert.equal(added.length,1,table); assert.deepEqual(next.tables[table].filter(x=>x.operation_id!==id),old.tables[table]);
   }
   assert.deepEqual(JSON.parse(next.tables.hosted_operations.find(x=>x.operation_id===id).result),result);
 }
 async function frames() { await sleep(100); assert.deepEqual(sc.messages,Array.from({length:rev-1},(_,i)=>({type:'invalidate',revision:i+2}))); assert.deepEqual(sb.messages,[]); }
 async function unchanged(id, action, args, expected) {
   const old=await cf(); assert.deepEqual(await call('fixture_mutate',[C,c,id,action,args],C),expected);
   const next=await cf(); assert.deepEqual(next.schema,old.schema);
   for(const table of Object.keys(old.tables)) if(!['hosted_operations','local_operations'].includes(table))
     assert.deepEqual(next.tables[table],old.tables[table],`${id}: ${table}`);
   assert.deepEqual(next.tables.fixture_revision,old.tables.fixture_revision); bookkeeping(old,next,id,expected);
   assert.equal((await cd()).revision,rev); await frames();
   assert.deepEqual(await call('fixture_mutate',[C,c,id,action,args],C),expected); assert.deepEqual(await cf(),next); await frames();
 }
 // Empty savings and absent investment rewards are business no-ops.
 await unchanged('false-withdraw','withdraw',[1,1],[false,'Nothing to take out']);
 await unchanged('zero-before','collect_rewards',[1,'invest'],0);
 await unchanged('false-term','invest',[1,'unknown',1],[false,'Unknown bond']);
 const effects={
   child:{tables:['kids'],wallet:500,savings:0},
   gift:{tables:['kids','transactions','history'],wallet:600,savings:0,tx:['gift',100]},
   save:{tables:['kids','transactions','history'],wallet:500,savings:100,tx:['save',100]},
   withdraw:{tables:['kids','transactions','history'],wallet:540,savings:60,tx:['withdraw',40]},
   spend:{tables:['kids','transactions','history'],wallet:520,savings:60,tx:['spend',-20]},
   invest:{tables:['kids','bonds','transactions','history'],wallet:420,savings:60,tx:['invest',100]},
   collect:{tables:['kids','bonds','transactions','history'],wallet:520,savings:60,tx:['bond_payout',100]},
   drain:{tables:['kids','transactions','history'],wallet:0,savings:60,tx:['spend',-520]},
 };
 async function changed(id, action, args, expected) {
   const old=await cf(), result=await call('fixture_mutate',[C,c,id,action,args],C);
   assert.deepEqual(result,expected); assert.equal((await cd()).revision,++rev);
   const next=await cf(); assert.notDeepEqual(domain.map(t=>next.tables[t]),domain.map(t=>old.tables[t]));
   assert.deepEqual(next.tables.fixture_revision,[{version:1,revision:rev}]); bookkeeping(old,next,id,expected);
   const effect=effects[id]; assert.deepEqual(next.schema,old.schema);
   for (const table of Object.keys(old.tables)) if(![...domain,'hosted_operations','local_operations','fixture_revision','sqlite_sequence'].includes(table))
     assert.deepEqual(next.tables[table],old.tables[table],`${id}: ${table}`);
   for (const table of ['transactions','history'])
     for (const row of old.tables[table]) assert.deepEqual(next.tables[table].find(x=>x.id===row.id),row);
   for (const table of domain) assert.equal(JSON.stringify(next.tables[table])!==JSON.stringify(old.tables[table]),effect.tables.includes(table),`${id}: ${table}`);
   assert.equal(next.tables.kids[0].wallet_cents,effect.wallet); assert.equal(next.tables.kids[0].savings_cents,effect.savings);
   if (effect.tx) {
     assert.equal(next.tables.transactions.length,old.tables.transactions.length+1);
     const added=next.tables.transactions.find(x=>!old.tables.transactions.some(y=>x.id===y.id));
     assert.deepEqual([added.kind,added.amount_cents,added.kid_id],[...effect.tx,1]);
     const previous=old.tables.kids[0], current=next.tables.kids[0];
     const allowed=['wallet_cents','savings_cents',...(id==='save'||id==='withdraw'?['savings_accrued_ts']:[])];
     for (const key of Object.keys(previous)) if(!allowed.includes(key)) assert.deepEqual(current[key],previous[key],`${id}: kid.${key}`);
     assert.equal(next.tables.history.length,old.tables.history.length+1);
   } else { assert.equal(next.tables.kids.length,old.tables.kids.length+1); assert.equal(next.tables.kids[1].name,'Second'); }
   await frames();
   assert.deepEqual(await call('fixture_mutate',[C,c,id,action,args],C),expected);
   assert.deepEqual(await cf(),next); assert.equal((await cd()).revision,rev); await frames();
 }
 await changed('child','add_kid',['Second'],true);
 await changed('gift','gift',[1,100],true);
 await changed('save','save',[1,100],[true,'Saved!']);
 await changed('withdraw','withdraw',[1,40],[true,'Done!']);
 await changed('spend','spend',[1,20],[true,'Spent!']);
 await changed('invest','invest',[1,'1mo',100],[true,'Locked in!']);
 // Advance the disposable object's read clock without writing any table or changing auth time.
 const clockBefore=await cf(), dashboardBefore=await cd();
 await call('fixture_clock',[C,c,7*86400],C);
 const dashboardAfter=await cd(); assert.equal(dashboardAfter.revision,dashboardBefore.revision);
 assert.ok(dashboardAfter.kids[0].pending_interest_cents > dashboardBefore.kids[0].pending_interest_cents);
 assert.deepEqual(await cf(),clockBefore); await frames();
 await call('fixture_clock',[C,c,0],C);
 let invested=await cf();
 assert.equal(invested.tables.kids[0].wallet_cents,420);
 assert.equal(invested.tables.kids[0].savings_cents,60);
 assert.equal(invested.tables.bonds[0].principal_cents,100);
 assert.deepEqual(invested.tables.transactions.slice().sort((a,b)=>a.id-b.id).map(x=>x.kind),['gift','save','withdraw','spend','invest']);
 assert.equal(invested.tables.history.length,5);
 await call('fixture_mature_zero',[C,c],C);
 let mature=await cf(); assert.equal(mature.tables.bonds[0].collected,0);
 await changed('collect','collect_rewards',[1,'invest'],0);
 let collected=await cf(); assert.equal(collected.tables.bonds[0].collected,1);
 assert.equal(collected.tables.kids[0].wallet_cents,mature.tables.kids[0].wallet_cents+100);
 await unchanged('noop','collect_rewards',[1,'invest'],0);
 // Drain wallet, then exercise all remaining applicable false business outcomes.
 await changed('drain','spend',[1,1000000],[true,'Spent!']);
 await unchanged('false-save','save',[1,1],[false,'Too little to save']);
 await unchanged('false-spend','spend',[1,1],[false,'No coins to spend']);
 await unchanged('false-invest','invest',[1,'1mo',1],[false,'Need more coins first']);
 const noop=await cf();
 for(const stage of ['before_revision','after_revision']) {
   assert.match(await call('fixture_fault',[C,c,'rollback','withdraw',[1,10],stage],C,403), /synthetic fault/); assert.deepEqual(await cf(),noop);
 }
 const checkpoint=JSON.parse(fs.readFileSync(process.env.CHECKPOINT,'utf8')); checkpoint.fullC=await cf();
 fs.writeFileSync(process.env.CHECKPOINT,JSON.stringify(checkpoint));
 const snap=await full(); await call('fixture_corrupt',[]); const corrupt=await full();
 assert.notDeepEqual(snap,corrupt);
 await call('fixture_dashboard',[A,a],A,403); await call('fixture_mutate',[A,a,'schema','gift',[1,1]],A,403); assert.deepEqual(await full(),corrupt);
 for(const ws of [sa,sa2,sb,sc]) ws.close();
 console.log('PASS actual DO: setup/revision, raw RPC denial, atomic rollback, replay/failure, concurrent retry, auth/family sockets, schema fail-closed');
})().catch(e=>{console.error(e);process.exit(1)});
