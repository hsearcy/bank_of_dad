const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
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
  const setup = async family => {const r=await a.request.post(process.env.BASE_URL+'/__spike/setup',{data:{family}}); assert.equal(r.status(),200); return r.json();};
  const ta=await setup(F), tb=await setup(G);
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
  await Promise.all([connect(a,F,ta[0]),connect(b,F,ta[1])]);
  await Promise.all([a,b].map(p=>dashboard(p,5,0,5)));
  await a.locator('#dadBtn').click();
  await a.locator('.gift-amt').fill('2'); await a.locator('.gift-note').fill('UI gift'); await a.locator('.gift-btn').click();
  await Promise.all([a,b].map(p=>dashboard(p,7,0,7)));
  await quiet(); assert.deepEqual(frames,[['{"type":"invalidate"}'],['{"type":"invalidate"}']]);
  await a.locator('#closeDad').click();
  await b.locator('.btn-save').click(); await b.locator('.custom-amt').fill('1'); await b.locator('[data-act="save"]').click();
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
   await p.locator('.tx-note').filter({hasText:'UI gift'}).waitFor();
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
  await a.locator('.btn-spend').click(); await a.locator('.spend-reason').fill('UI spend'); await a.locator('.spend-amt').fill('0.5'); await a.locator('.modal-go').click();
  await dashboard(a,5.5,1,6.5);
  await connect(b,F,ta[1]); await dashboard(b,5.5,1,6.5);
  await b.reload(); await connect(b,F,ta[1]); await dashboard(b,5.5,1,6.5);
  const denied=await a.request.get(process.env.BASE_URL+'/api/state',{headers:{'x-spike-family':G,'x-spike-token':ta[0]}}); assert.equal(denied.status(),403);
  const deniedWrite=await a.request.post(process.env.BASE_URL+'/api/admin/gift',{headers:{'x-spike-family':G,'x-spike-token':ta[0]},data:{kid_id:1,amount_cents:900,note:'wrong'}}); assert.equal(deniedWrite.status(),403);
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
  // Hold a committed A gift completion across logout: no stale Dad render.
  await connect(a,F,ta[0]); await a.locator('#dadBtn').click();
  let releaseMutation, committed;
  const mutationGate=new Promise(r=>releaseMutation=r), commitSeen=new Promise(r=>committed=r);
  await a.route('**/api/admin/gift',async route=>{const response=await route.fetch(); committed(); await mutationGate; await route.fulfill({response});},{times:1});
  await a.locator('.gift-amt').fill('1'); await a.locator('.gift-btn').click(); await commitSeen;
  await a.locator('#spike-logout').click(); releaseMutation(); await quiet();
  assert.equal(await a.locator('#kids').textContent(),''); assert.equal(await a.locator('#spike-token').inputValue(),'');
  assert.equal(await a.locator('#dadPanel').evaluate(x=>x.classList.contains('hidden')),true);
  await dashboard(b,5,0,5); // no cross-family push or writes
  assert.equal(frames[1].length,beforeCrossFamily);
  await connect(a,F,ta[0]); await dashboard(a,6.5,1,7.5);
  // Hold history across logout, including modal content removal.
  let releaseHistory, historySeen;
  const historyGate=new Promise(r=>releaseHistory=r), gotHistory=new Promise(r=>historySeen=r);
  await a.route('**/api/transactions?*',async route=>{const response=await route.fetch();historySeen();await historyGate;await route.fulfill({response});},{times:1});
  await a.locator('.btn-history').click(); await gotHistory;
  await a.locator('#spike-logout').click(); releaseHistory(); await quiet();
  assert.equal(await a.locator('#kids').textContent(),'');
  assert.equal(await a.locator('#modal').evaluate(x=>x.classList.contains('hidden')),true);
  await b.locator('#spike-logout').click();
  assert.deepEqual(errors,[]);
  console.log('PASS: actual template/CSS/JS; two independent Chromium processes; gift/save/spend; push; reconnect/reload; scoped read/write/socket denial; held state/mutation/history and stale socket family/logout races.');
 } finally {await Promise.all(browsers.map(x=>x.close()));}
})().catch(e=>{console.error(e);process.exitCode=1;});
