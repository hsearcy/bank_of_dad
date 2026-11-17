const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
(async () => {
 const browsers = [];
 const errors = [];
 const F = 'a'.repeat(32), G = 'b'.repeat(32);
 const quiet = () => new Promise(r => setTimeout(r,450));
 try {
  for (let i=0;i<2;i++) browsers.push(await chromium.launch({headless:true,executablePath:process.env.CHROMIUM || '/usr/bin/chromium',args:['--no-sandbox']}));
  const [a,b] = await Promise.all(browsers.map(x => x.newPage()));
  const frames = [[],[]];
  for (const [i,p] of [a,b].entries()) {
   p.on('pageerror',e=>errors.push(e.message));
   p.on('websocket',ws=>ws.on('framereceived',e=>frames[i].push(e.payload)));
   await p.addInitScript(() => {const Real = WebSocket; window.sockets=[]; window.WebSocket=class extends Real {constructor(...args){super(...args); sockets.push(this);}};});
   await p.goto(process.env.BASE_URL);
  }
  // The production template differs only by the additional fixture script.
  const root = path.resolve(__dirname,'../..');
  const html = await (await a.request.get(process.env.BASE_URL)).text();
  assert.equal(html.replace('<script src="/static/synthetic.js"></script>',''),fs.readFileSync(path.join(root,'templates/index.html'),'utf8'));
  for (const file of ['app.js','style.css']) assert.equal(await (await a.request.get(process.env.BASE_URL+'/static/'+file)).text(),fs.readFileSync(path.join(root,'static',file),'utf8'));
  assert.equal(await a.evaluate(()=>navigator.serviceWorker.getRegistrations().then(x=>x.length)),0);
  // Driver uses the private worker port and per-run secret, never the dashboard ingress.
  const control = async (route, data) => {
   const r=await fetch(process.env.DRIVER_URL+'/__test/'+route,{method:'POST',headers:{'content-type':'application/json','x-test-secret':process.env.TEST_SECRET},body:JSON.stringify(data)});
   assert.equal(r.status,200); return r.json();
  };
  const setup=family=>control('setup',{family});
  const mutate=(family,token,action,args)=>control('mutate',{family,token,action,args,operation:crypto.randomUUID()});
  const ta=await setup(F), tb=await setup(G);
  const snapshot=family=>control('snapshot',{family});
  const beforeAll=await Promise.all([snapshot(F),snapshot(G)]);
  for (const snap of beforeAll) {
   assert.ok(Array.isArray(snap.schema));
   assert.ok(snap.tables.sqlite_sequence);
   assert.ok(snap.tables.kids);
   assert.deepEqual(await snapshot(snap === beforeAll[0] ? F : G),snap);
  }
  assert.equal((await a.request.get(process.env.BASE_URL+'/__test/mutate')).status(),405);
  assert.equal((await a.request.post(process.env.BASE_URL+'/__test/setup',{data:{family:F},headers:{'x-test-secret':process.env.TEST_SECRET}})).status(),405);
  assert.equal((await a.request.post(process.env.BASE_URL+'/__test/snapshot',{data:{family:F},headers:{'x-test-secret':process.env.TEST_SECRET}})).status(),405);
  assert.equal((await fetch(process.env.DRIVER_URL+'/__test/snapshot',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({family:F})})).status,403);
  assert.equal((await fetch(process.env.DRIVER_URL+'/__test/setup',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({family:F})})).status,403);
  const connect = async (p,f,t) => {
   await p.locator('#spike-family').selectOption(f);
   await p.locator('#spike-token').fill(t);
   await p.locator('#spike-connect').click();
   await p.waitForFunction(()=>document.querySelector('#spike-status').textContent==='connected');
  };
  const dashboard = async (p,wallet,savings,total) => {
   await p.waitForFunction(([w,s,t])=>document.querySelector('.coins')?.textContent.includes(`$${w.toFixed(2)} to spend`) && document.querySelector('.s-val')?.textContent===`$${s.toFixed(2)}` && document.querySelector('.big')?.textContent===`$${t.toFixed(2)}`,[wallet,savings,total]);
   assert.equal(await p.locator('.name').textContent(),'Synthetic child');
  };
  // A socket opening is not proof the initial authorized snapshot succeeded.
  await a.route('**/api/state', route=>route.fulfill({status:500,contentType:'text/plain',body:'unavailable'}), {times:1});
  await a.locator('#spike-token').fill(ta[0]); await a.locator('#spike-connect').click();
  await a.waitForFunction(()=>document.querySelector('#spike-status').textContent==='snapshot failed');
  assert.equal(await a.locator('#kids').textContent(),'');
  await Promise.all([connect(a,F,ta[0]),connect(b,F,ta[1])]);
  assert.match(await a.evaluate(()=>api('/api/not-supported').then(()=>'',e=>e.message)), /failed or unsupported/);
  assert.match(await a.evaluate(()=>api('/api/not-supported',{x:1}).then(()=>'',e=>e.message)), /Read-only/);
  await Promise.all([a,b].map(p=>dashboard(p,5,0,5)));
  const before=await a.evaluate(()=>Promise.all([api('/api/state'),api('/api/transactions?kid_id=1')]));
  // Disabled controls and direct handler calls cannot create optimistic effects.
  for (const p of [a,b]) {
   const writes=[]; const capture=req=>{if (req.method() !== 'GET') writes.push(req.url());};
   p.on('request',capture);
   const attempted=await p.evaluate(async()=>{
    document.querySelector('#newKidName').value='Blocked child';
    Object.assign(STATE.kids[0], {pending_cents:300, pending_allowance_cents:100, pending_interest_cents:100, pending_bonds_cents:100}); render();
    const disabled=[...document.querySelectorAll('#dadBtn,#dadPanel button:not(#closeDad),#dadPanel input,.reward-btn,.btn-save,.btn-bond,.btn-spend')].every(x=>x.disabled);
    const view=()=>JSON.stringify({kids:document.querySelector('#kids').innerHTML,modal:document.querySelector('#modal').outerHTML,dad:document.querySelector('#dadPanel').outerHTML,confetti:document.querySelector('#confetti').innerHTML,state:STATE});
    const before=view();
    const rewards=[...document.querySelectorAll('.reward-btn')];
    if (rewards.length !== 3) throw Error('expected all three reward kinds');
    for (const el of rewards) {el.click(); el.onclick({currentTarget:el,clientX:0,clientY:0}); onCollect(STATE.kids[0],el.dataset.kind,{currentTarget:el,clientX:0,clientY:0});}
    for (const sel of ['.btn-save','.btn-bond','.btn-spend','#dadBtn','#addKidBtn','#changePinBtn','.ff']) {
     const elements=[...document.querySelectorAll(sel)]; if (!elements.length) throw Error(`missing ${sel}`);
     for (const el of elements) {el.click(); await el.onclick?.({currentTarget:el});}
    }
    await Promise.all([openSaveModal(STATE.kids[0]),openBondModal(STATE.kids[0]),openSpendModal(STATE.kids[0]),openEditHistory(STATE.kids[0]),openDad(),ensurePinSetup(),changePin(),renderDadPanel(),renderDadRates()]);
    await new Promise(r=>setTimeout(r,100));
    return {disabled,unchanged:before===view()};
   });
   assert.deepEqual(attempted,{disabled:true,unchanged:true});
   assert.deepEqual(writes,[]); p.off('request',capture);
   assert.equal(await p.locator('.gift-btn,.alw-btn,.edithist-btn,#saveRates,.etx-save,.etx-del').count(),0);
   await p.evaluate(()=>refresh());
  }
  // Hidden money controls cannot be clicked; exercise visible read-only controls.
  for (const p of [a,b]) {
   for (const sel of ['#dadBtn','.btn-save','.btn-bond','.btn-spend','.reward-btn']) assert.equal(await p.locator(sel+':visible').count(),0);
   await p.locator('.btn-whatif').click(); await p.locator('.wi-save').fill('1'); await p.locator('#modal .modal-go').click();
   await p.locator('.rng').first().click(); await p.locator('.btn-expand').click(); await p.locator('.graph-close').click();
   await p.locator('.btn-history').click(); await p.locator('#modal .close').click();
  }
  const mutators=['/api/collect','/api/save','/api/withdraw','/api/bond','/api/spend','/api/admin/verify','/api/admin/pin','/api/admin/gift','/api/admin/kid','/api/admin/kid/update','/api/admin/kid/allowance','/api/admin/tx/delete','/api/admin/tx/edit','/api/admin/rates','/api/admin/fastforward'];
  for (const route of ['/', '/static/app.js', ...mutators, '/unknown']) {
   const r=await fetch(process.env.DRIVER_URL+route,{method:'POST',headers:{'x-spike-family':F,'x-spike-token':ta[0]},body:'{}'});
   assert.equal(r.status,405,'internal dashboard '+route);
  }
  for (const route of [...mutators,'/api/unsupported','/anything']) {
   const r=await a.request.post(process.env.BASE_URL+route,{headers:{'x-spike-family':F,'x-spike-token':ta[0]},data:{kid_id:1,amount_cents:500,note:'forbidden'}});
   assert.equal(r.status(),405,route);
  }
  for (const method of ['PUT','PATCH','DELETE']) {
   const r=await a.request.fetch(process.env.BASE_URL+'/api/save',{method,data:{kid_id:1,amount_cents:100}}); assert.equal(r.status(),405);
  }
  const deniedWrite=await a.request.post(process.env.BASE_URL+'/api/admin/gift',{headers:{'x-spike-family':G,'x-spike-token':ta[0]},data:{kid_id:1,amount_cents:900,note:'wrong'}}); assert.equal(deniedWrite.status(),405);
  assert.deepEqual(await Promise.all([snapshot(F),snapshot(G)]),beforeAll);
  const after=await a.evaluate(()=>Promise.all([api('/api/state'),api('/api/transactions?kid_id=1')]));
  for (const key of ['wallet_cents','savings_cents','owned_cents','history']) assert.deepEqual(after[0].kids[0][key],before[0].kids[0][key]);
  assert.deepEqual(after[1].transactions,before[1].transactions);
  const giftResult=await mutate(F,ta[0],'gift',[1,200,'driver gift']);
  assert.equal(giftResult,true);
  const afterGift=await snapshot(F);
  assert.notDeepEqual(afterGift,beforeAll[0], 'snapshot detects authorized transaction');
  assert.notDeepEqual(afterGift.tables.kids,beforeAll[0].tables.kids);
  assert.notDeepEqual(afterGift.tables.transactions,beforeAll[0].tables.transactions);
  assert.notDeepEqual(afterGift.tables.sqlite_sequence,beforeAll[0].tables.sqlite_sequence);
  assert.deepEqual(afterGift.schema,beforeAll[0].schema);
  assert.deepEqual(await snapshot(G),beforeAll[1]);
  await Promise.all([a,b].map(p=>dashboard(p,7,0,7)));
  await quiet(); assert.deepEqual(frames.map(xs=>xs.map(JSON.parse)),[[{type:'invalidate',revision:2}],[{type:'invalidate',revision:2}]]);
  await mutate(F,ta[1],'save',[1,100]);
  await Promise.all([a,b].map(p=>dashboard(p,6,1,7)));
  await quiet(); assert.equal(frames[0].length,2); assert.equal(frames[1].length,2);
  // Charts contain server timestamps: compare concrete state plus history as
  // well as the stable dashboard amounts instead of a second ledger model.
  const state = async(p)=>p.evaluate(()=>api('/api/state'));
  const sa=await state(a), sb=await state(b);
  for (const key of ['wallet_cents','savings_cents','owned_cents','history','bonds','name'])
   assert.deepEqual(sa.kids[0][key],sb.kids[0][key]);
  assert.deepEqual(sa.config,sb.config);
  for (const p of [a,b]) {
   await p.locator('.btn-history').click();
   await p.locator('.tx-note').filter({hasText:'driver gift'}).waitFor();
   assert.equal(await p.locator('.tx-row').count(),2);
   await p.locator('#modal .close').click();
  }
  // An older same-context refresh must not overwrite a newer response.
  let releaseOld, oldSeen;
  const oldGate = new Promise(r=>releaseOld=r), oldReceived = new Promise(r=>oldSeen=r);
  await b.route('**/api/state',async route=>{const response=await route.fetch();oldSeen();await oldGate;await route.fulfill({response});},{times:1});
  await b.evaluate(()=>{window.oldRefresh=refresh();}); await oldReceived;
  // A fresh actual snapshot overtakes the held one. Count renders rather than
  // altering either server response to manufacture a different balance.
  await b.evaluate(()=>{window.renders=0;const original=render;window.render=()=>{renders++;original();};});
  await b.evaluate(()=>refresh());
  const newerRenders=await b.evaluate(()=>renders);
  releaseOld(); await b.evaluate(()=>oldRefresh);
  assert.equal(await b.evaluate(()=>renders),newerRenders);
  // An older 500 from the real reload path must not overwrite newer success.
  let releaseFailure, failureSeen;
  const failureGate=new Promise(r=>releaseFailure=r), gotFailure=new Promise(r=>failureSeen=r);
  await b.route('**/api/state',async route=>{failureSeen(); await failureGate; await route.fulfill({status:500,body:'unavailable'});},{times:1});
  await b.evaluate(()=>{window.beforeFailureRenders=renders; document.dispatchEvent(new Event('visibilitychange'));});
  await gotFailure;
  const finished=b.waitForResponse(r=>r.url().endsWith('/api/state') && r.status()===200);
  await b.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await finished;
  await b.waitForFunction(()=>renders>beforeFailureRenders && document.querySelector('#spike-status').textContent==='connected');
  const failedResponse=b.waitForResponse(r=>r.url().endsWith('/api/state') && r.status()===500);
  releaseFailure(); await failedResponse;
  await quiet();
  assert.equal(await b.locator('#spike-status').textContent(),'connected');
  await dashboard(b,6,1,7);
  // Newer failure supersedes an already received older success.
  let releaseSuccess, seenSuccess;
  const successGate=new Promise(r=>releaseSuccess=r), gotSuccess=new Promise(r=>seenSuccess=r);
  let reverseCalls=0;
  const reverseRoute=async route=>{
   if (++reverseCalls === 1) {const response=await route.fetch(); seenSuccess(); await successGate; await route.fulfill({response});}
   else await route.fulfill({status:500,body:'unavailable'});
  };
  await b.route('**/api/state',reverseRoute);
  await b.evaluate(()=>{window.olderSuccess=refresh();}); await gotSuccess;
  const countBeforeFailure=await b.evaluate(()=>renders);
  assert.match(await b.evaluate(()=>refresh().then(()=>'unexpected',e=>e.message)),/failed/);
  releaseSuccess(); assert.equal(await b.evaluate(()=>olderSuccess),false);
  await b.unroute('**/api/state',reverseRoute); assert.equal(reverseCalls,2);
  assert.equal(await b.evaluate(()=>renders),countBeforeFailure);
  await dashboard(b,6,1,7);
  // Offline peer misses a real commit, then gets an authorized fresh snapshot.
  await b.evaluate(()=>sockets.at(-1).close());
  await b.waitForFunction(()=>document.querySelector('#kids').children.length===0);
  assert.equal(await b.locator('#spike-token').inputValue(),ta[1]);
  await mutate(F,ta[0],'spend',[1,50,'driver spend']);
  await dashboard(a,5.5,1,6.5);
  await a.locator('.btn-history').click();
  await a.locator('.tx-note').filter({hasText:'driver spend'}).waitFor();
  assert.equal(await a.locator('.tx-row').count(),3);
  await a.locator('#modal .close').click();
  await b.waitForFunction(()=>document.querySelector('#spike-status').textContent==='connected'); await dashboard(b,5.5,1,6.5);
  await b.reload(); await connect(b,F,ta[1]); await dashboard(b,5.5,1,6.5);
  const denied=await a.request.get(process.env.BASE_URL+'/api/state',{headers:{'x-spike-family':G,'x-spike-token':ta[0]}}); assert.equal(denied.status(),403);
  const deniedDriver=await fetch(process.env.DRIVER_URL+'/__test/mutate',{method:'POST',headers:{'content-type':'application/json','x-test-secret':process.env.TEST_SECRET},body:JSON.stringify({family:G,token:ta[0],operation:crypto.randomUUID(),action:'gift',args:[1,900,'wrong']})}); assert.equal(deniedDriver.status,403);
  assert.equal(await b.evaluate(([f,t])=>new Promise(resolve=>{const w=new WebSocket(`ws://${location.host}/__spike/socket?family=${f}&token=${t}`); w.onopen=()=>{w.close();resolve(false)}; w.onerror=()=>resolve(true)}),[G,ta[0]]),true);
  // Hold a real A state response; switch to B then deliver it and captured A callbacks.
  let release, captured;
  const gate = new Promise(r=>release=r);
  await b.route('**/api/state',async route=>{const response=await route.fetch(); captured?.(); await gate; await route.fulfill({response});},{times:1});
  const received = new Promise(r=>captured=r);
  await b.evaluate(()=>{const live = sockets.findLast(s => s.readyState === WebSocket.OPEN && typeof s.onmessage === 'function'); if (!live) throw Error('active family socket missing'); window.oldMessage=live.onmessage; window.oldClose=live.onclose; window.held=refresh().catch(e=>e.name);});
  await received;
  await b.locator('#spike-family').selectOption(G);
  assert.equal(await b.locator('#spike-token').inputValue(),'');
  await connect(b,G,tb[0]); await dashboard(b,5,0,5);
  release(); assert.equal(await b.evaluate(()=>held),'AbortError');
  await b.evaluate(()=>{oldMessage({data:'{"type":"invalidate"}'}); oldClose();});
  await dashboard(b,5,0,5);
  // A failed reload from the old family cannot mark the new family failed.
  let releaseCrossFailure, sawCrossFailure;
  const crossFailureGate=new Promise(r=>releaseCrossFailure=r), crossFailureSeen=new Promise(r=>sawCrossFailure=r);
  await b.route('**/api/state',async route=>{sawCrossFailure(); await crossFailureGate; await route.fulfill({status:500,body:'unavailable'});},{times:1});
  await b.evaluate(()=>document.dispatchEvent(new Event('visibilitychange'))); await crossFailureSeen;
  await b.locator('#spike-family').selectOption(F); await connect(b,F,ta[1]); await dashboard(b,5.5,1,6.5);
  const crossFailedResponse=b.waitForResponse(r=>r.url().endsWith('/api/state') && r.status()===500);
  releaseCrossFailure(); await crossFailedResponse; await quiet();
  assert.equal(await b.locator('#spike-status').textContent(),'connected'); await dashboard(b,5.5,1,6.5);
  await b.locator('#spike-family').selectOption(G); await connect(b,G,tb[0]); await dashboard(b,5,0,5);
  const beforeCrossFamily = frames[1].length;
  // A test-driver mutation never invalidates another family.
  await mutate(F,ta[0],'gift',[1,100,'cross-family test']);
  await quiet(); await dashboard(b,5,0,5);
  assert.equal(frames[1].length,beforeCrossFamily);
  await dashboard(a,6.5,1,7.5);
  // Hold history across logout, including modal content removal.
  let releaseHistory, historySeen;
  const historyGate=new Promise(r=>releaseHistory=r), gotHistory=new Promise(r=>historySeen=r);
  await a.route('**/api/transactions?*',async route=>{const response=await route.fetch();historySeen();await historyGate;await route.fulfill({response});},{times:1});
  await a.locator('.btn-history').click(); await gotHistory;
  await a.locator('#spike-logout').click(); releaseHistory(); await quiet();
  assert.equal(await a.locator('#kids').textContent(),'');
  assert.equal(await a.locator('#modal').evaluate(x=>x.classList.contains('hidden')),true);
  await b.locator('#spike-logout').click();
  assert.equal(await b.locator('#spike-token').inputValue(),'');
  // A visible-tab resume reconciles a deliberately dropped push with polling off.
  await Promise.all([connect(a,F,ta[0]),connect(b,F,ta[1])]);
  await b.evaluate(()=>{sockets.findLast(s=>s.readyState===WebSocket.OPEN).onmessage=null;});
  await mutate(F,ta[0],'gift',[1,100,'resume']);
  await dashboard(a,7.5,1,8.5); await quiet(); await dashboard(b,6.5,1,7.5);
  await b.evaluate(()=>{Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'}); document.dispatchEvent(new Event('visibilitychange'));});
  await dashboard(b,7.5,1,8.5);
  await Promise.all([a,b].map(p=>p.locator('#spike-logout').click()));
  // Accelerate only the synthetic reconciliation timer, not the regular app poll.
  await b.addInitScript(() => {window.__BOD_SYNTHETIC_SAFETY_MS__=3000;});
  await b.reload(); await connect(b,F,ta[1]);
  const beforeLoss = frames[1].length;
  await control('mutate',{family:F,token:ta[0],operation:crypto.randomUUID(),action:'gift',args:[1,50,'lost notification'],dropNotification:true});
  await dashboard(b,8,1,9);
  assert.equal(frames[1].length,beforeLoss, 'postcommit notification was suppressed');
  // Hold a real old-revision snapshot while a real committed mutation and its
  // push install a newer snapshot; releasing the old one must not roll back.
  let releaseRevision, revisionSeen;
  const revisionGate=new Promise(r=>releaseRevision=r), gotRevision=new Promise(r=>revisionSeen=r);
  await b.route('**/api/state',async route=>{const response=await route.fetch(); revisionSeen(); await revisionGate; await route.fulfill({response});},{times:1});
  await b.evaluate(()=>{window.heldRevision=refresh();}); await gotRevision;
  await mutate(F,ta[0],'gift',[1,25,'revision ordering']);
  await dashboard(b,8.25,1,9.25);
  releaseRevision(); assert.equal(await b.evaluate(()=>heldRevision),false);
  await dashboard(b,8.25,1,9.25);
  // Real time-derived interest changes while durable revision stays equal.
  const earlier = await state(b);
  await control('clock',{family:F,token:ta[0],seconds:7*86400});
  await b.evaluate(()=>refresh());
  const later=await b.evaluate(()=>STATE);
  assert.equal(later.revision,earlier.revision);
  assert.ok(later.kids[0].pending_interest_cents > earlier.kids[0].pending_interest_cents);
  // Older/lower and duplicate invalidations must not trigger snapshot requests.
  let requests=0; const count=req=>{if(req.url().endsWith('/api/state')) requests++;}; b.on('request',count);
  await b.evaluate(rev=>{const w=sockets.findLast(s=>s.readyState===WebSocket.OPEN && s.onmessage); for(const n of [rev,rev-1,rev, -1]) w.onmessage({data:JSON.stringify({type:'invalidate',revision:n})});},later.revision);
  await quiet(); assert.equal(requests,0); b.off('request',count);
  // A newer request returning an older revision cannot roll the dashboard back.
  await b.route('**/api/state',async route=>{const r=await route.fetch(); const body=await r.json(); body.revision--; body.kids[0].wallet_cents=1; await route.fulfill({json:body});},{times:1});
  assert.equal(await b.evaluate(()=>refresh()),false);
  assert.equal(await b.evaluate(()=>STATE.kids[0].wallet_cents),825);
  // Intentional offline state cancels retries until online resumes.
  await b.evaluate(()=>{Object.defineProperty(navigator,'onLine',{configurable:true,value:false}); window.dispatchEvent(new Event('offline'));});
  assert.equal(await b.locator('#spike-status').textContent(),'offline');
  const offlineCount=await b.evaluate(()=>sockets.length); await new Promise(r=>setTimeout(r,1100));
  assert.equal(await b.evaluate(()=>sockets.length),offlineCount);
  await b.evaluate(()=>{Object.defineProperty(navigator,'onLine',{configurable:true,value:true}); window.dispatchEvent(new Event('online'));});
  await b.waitForFunction(()=>document.querySelector('#spike-status').textContent==='connected');
  // Revocation makes the next reconciliation fail closed and stops retries.
  await control('revoke',{family:F,token:ta[1]});
  await b.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await b.waitForFunction(()=>document.querySelector('#spike-status').textContent==='access denied');
  assert.equal(await b.locator('#kids').textContent(),'');
  const closedCount=await b.evaluate(()=>sockets.length); await new Promise(r=>setTimeout(r,1200));
  assert.equal(await b.evaluate(()=>sockets.length),closedCount);
  // A real expiring grant is denied by the DO and reconciliation clears the UI.
  const short=await control('short-grant',{family:F,token:ta[0]});
  await connect(b,F,short);
  await b.waitForFunction(()=>document.querySelector('#spike-status').textContent==='access denied',null,{timeout:10000});
  assert.equal(await b.locator('#kids').textContent(),'');
  assert.deepEqual(errors,[]);
  console.log('PASS: actual template/CSS/JS; two independent Chromium processes; driver-only gift/save/spend; read-only UI and HTTP; push; reconnect/reload; scoped read/socket denial; held state/history and stale socket family/logout races.');
 } finally {await Promise.all(browsers.map(x=>x.close()));}
})().catch(e=>{console.error(e);process.exitCode=1;});
