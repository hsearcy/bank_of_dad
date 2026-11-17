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
  assert.equal((await a.request.get(process.env.BASE_URL+'/__test/mutate')).status(),405);
  assert.equal((await a.request.post(process.env.BASE_URL+'/__test/setup',{data:{family:F},headers:{'x-test-secret':process.env.TEST_SECRET}})).status(),405);
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
  // Hidden money controls cannot be clicked; exercise visible read-only controls.
  for (const p of [a,b]) {
   for (const sel of ['#dadBtn','.btn-save','.btn-bond','.btn-spend','.reward-btn']) assert.equal(await p.locator(sel+':visible').count(),0);
   await p.locator('.btn-whatif').click(); await p.locator('.wi-save').fill('1'); await p.locator('#modal .modal-go').click();
   await p.locator('.rng').first().click(); await p.locator('.btn-expand').click(); await p.locator('.graph-close').click();
   await p.locator('.btn-history').click(); await p.locator('#modal .close').click();
  }
  for (const route of ['/', '/static/app.js', '/api/admin/gift', '/api/save', '/api/spend', '/unknown']) {
   const r=await fetch(process.env.DRIVER_URL+route,{method:'POST',headers:{'x-spike-family':F,'x-spike-token':ta[0]},body:'{}'});
   assert.equal(r.status,405,'internal dashboard '+route);
  }
  for (const route of ['/api/admin/gift','/api/save','/api/spend','/api/withdraw','/api/collect','/api/bond','/api/unsupported','/anything']) {
   const r=await a.request.post(process.env.BASE_URL+route,{headers:{'x-spike-family':F,'x-spike-token':ta[0]},data:{kid_id:1,amount_cents:500,note:'forbidden'}});
   assert.equal(r.status(),405,route);
  }
  for (const method of ['PUT','PATCH','DELETE']) {
   const r=await a.request.fetch(process.env.BASE_URL+'/api/save',{method,data:{kid_id:1,amount_cents:100}}); assert.equal(r.status(),405);
  }
  const after=await a.evaluate(()=>Promise.all([api('/api/state'),api('/api/transactions?kid_id=1')]));
  for (const key of ['wallet_cents','savings_cents','owned_cents','history']) assert.deepEqual(after[0].kids[0][key],before[0].kids[0][key]);
  assert.deepEqual(after[1].transactions,before[1].transactions);
  await mutate(F,ta[0],'gift',[1,200,'driver gift']);
  await Promise.all([a,b].map(p=>dashboard(p,7,0,7)));
  await quiet(); assert.deepEqual(frames,[['{"type":"invalidate"}'],['{"type":"invalidate"}']]);
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
  // Offline peer misses a real commit, then gets an authorized fresh snapshot.
  await b.evaluate(()=>sockets.at(-1).close());
  await b.waitForFunction(()=>document.querySelector('#kids').children.length===0);
  await mutate(F,ta[0],'spend',[1,50,'driver spend']);
  await dashboard(a,5.5,1,6.5);
  await a.locator('.btn-history').click();
  await a.locator('.tx-note').filter({hasText:'driver spend'}).waitFor();
  assert.equal(await a.locator('.tx-row').count(),3);
  await a.locator('#modal .close').click();
  await connect(b,F,ta[1]); await dashboard(b,5.5,1,6.5);
  await b.reload(); await connect(b,F,ta[1]); await dashboard(b,5.5,1,6.5);
  const denied=await a.request.get(process.env.BASE_URL+'/api/state',{headers:{'x-spike-family':G,'x-spike-token':ta[0]}}); assert.equal(denied.status(),403);
  const deniedDriver=await fetch(process.env.DRIVER_URL+'/__test/mutate',{method:'POST',headers:{'content-type':'application/json','x-test-secret':process.env.TEST_SECRET},body:JSON.stringify({family:G,token:ta[0],operation:crypto.randomUUID(),action:'gift',args:[1,900,'wrong']})}); assert.equal(deniedDriver.status,403);
  const deniedWrite=await a.request.post(process.env.BASE_URL+'/api/admin/gift',{headers:{'x-spike-family':G,'x-spike-token':ta[0],'x-spike-operation':'12345678-1234-4123-8123-123456789abc'},data:{kid_id:1,amount_cents:900,note:'wrong'}}); assert.equal(deniedWrite.status(),405);
  assert.equal(await b.evaluate(([f,t])=>new Promise(resolve=>{const w=new WebSocket(`ws://${location.host}/__spike/socket?family=${f}&token=${t}`); w.onopen=()=>{w.close();resolve(false)}; w.onerror=()=>resolve(true)}),[G,ta[0]]),true);
  // Hold a real A state response; switch to B then deliver it and captured A callbacks.
  let release, captured;
  const gate = new Promise(r=>release=r);
  await b.route('**/api/state',async route=>{const response=await route.fetch(); captured?.(); await gate; await route.fulfill({response});},{times:1});
  const received = new Promise(r=>captured=r);
  await b.evaluate(()=>{const live = sockets.findLast(s => s.readyState === WebSocket.OPEN && typeof s.onmessage === 'function'); if (!live) throw Error('active family socket missing'); window.oldMessage=live.onmessage; window.oldClose=live.onclose; window.held=refresh().catch(e=>e.name);});
  await received;
  await connect(b,G,tb[0]); await dashboard(b,5,0,5);
  release(); assert.equal(await b.evaluate(()=>held),'AbortError');
  await b.evaluate(()=>{oldMessage({data:'{"type":"invalidate"}'}); oldClose();});
  await dashboard(b,5,0,5);
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
  // A visible-tab resume reconciles a deliberately dropped push with polling off.
  await Promise.all([connect(a,F,ta[0]),connect(b,F,ta[1])]);
  await b.evaluate(()=>{sockets.findLast(s=>s.readyState===WebSocket.OPEN).onmessage=null;});
  await mutate(F,ta[0],'gift',[1,100,'resume']);
  await dashboard(a,7.5,1,8.5); await quiet(); await dashboard(b,6.5,1,7.5);
  await b.evaluate(()=>{Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'}); document.dispatchEvent(new Event('visibilitychange'));});
  await dashboard(b,7.5,1,8.5);
  await Promise.all([a,b].map(p=>p.locator('#spike-logout').click()));
  assert.deepEqual(errors,[]);
  console.log('PASS: actual template/CSS/JS; two independent Chromium processes; driver-only gift/save/spend; read-only UI and HTTP; push; reconnect/reload; scoped read/socket denial; held state/history and stale socket family/logout races.');
 } finally {await Promise.all(browsers.map(x=>x.close()));}
})().catch(e=>{console.error(e);process.exitCode=1;});
