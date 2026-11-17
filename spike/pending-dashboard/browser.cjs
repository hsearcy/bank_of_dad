// Disposable loopback only: actual dashboard, separate privileged fixture driver.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE);
const assert = require('node:assert/strict'), fs=require('node:fs'), path=require('node:path');
const base=process.env.BASE_URL, internal=process.env.DRIVER_URL, F='a'.repeat(32), G='b'.repeat(32);
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function driver(family,method,args=[]) {
 const r=await fetch(internal+'/__unsafe_test_driver/call',{method:'POST',headers:{'content-type':'application/json','x-test-secret':process.env.TEST_SECRET},body:JSON.stringify({family,method,args})});
 assert.equal(r.status,200,method); return r.json();
}
async function connect(p,f,t) {
 await p.locator('#spike-family').selectOption(f); await p.locator('#spike-token').fill(t); await p.locator('#spike-connect').click();
 await p.waitForFunction(()=>document.querySelector('#spike-status').textContent==='connected',{timeout:15000});
}
async function contains(p,s) {await p.waitForFunction(s=>document.querySelector('#spike-pending').textContent.includes(s),s,{timeout:15000});}
(async()=>{
 const browsers=[];
 try {
  for(let i=0;i<2;i++) browsers.push(await chromium.launch({headless:true,executablePath:'/usr/bin/chromium',args:['--no-sandbox']}));
  const pages=await Promise.all(browsers.map(b=>b.newPage())), [a,b]=pages;
  for(const p of pages) {
   await p.addInitScript(()=>{window.__BOD_SYNTHETIC_SAFETY_MS__=60000; const interval=window.setInterval; window.setInterval=(fn,ms,...args)=>{if(ms===60000) window.reconcile=fn; return interval(fn,ms,...args);}; const WS=window.WebSocket; window.testSockets=[]; window.WebSocket=class extends WS {constructor(...args){super(...args);window.testSockets.push(this)}};});
   await p.goto(base);
  }
  const root=path.resolve(__dirname,'../..');
  const html=await (await a.request.get(base)).text();
  assert.equal(html.replace('<script src="/static/synthetic.js"></script>',''),fs.readFileSync(path.join(root,'templates/index.html'),'utf8'));
  for(const f of ['app.js','style.css']) assert.equal(await (await a.request.get(base+'/static/'+f)).text(),fs.readFileSync(path.join(root,'static',f),'utf8'));
  const [t1,t2,other]=await driver(F,'setup',[F]); const [gt]=await driver(G,'setup',[G]);
  await Promise.all([connect(a,F,t1),connect(b,F,t2)]); await Promise.all(pages.map(p=>contains(p,'No pending operation.')));
  const xss='<img src=x onerror=window.xss=1>';
  assert.equal(await driver(F,'mutate',[F,t1,'kid','add_kid',[xss]]),true);
  await Promise.all(pages.map(p=>contains(p,xss)));
  for(const p of pages) {assert.equal(await p.evaluate(()=>window.xss),undefined); assert.equal(await p.locator('#spike-pending img').count(),0);}
  await driver(F,'acknowledge',[F,t1,'kid',true]); await Promise.all(pages.map(p=>contains(p,'No pending operation.')));
  const before=await (await fetch(base+'/api/state',{headers:{'x-spike-family':F,'x-spike-token':t1}})).json();
  const failure=[false,'Nothing to take out']; assert.deepEqual(await driver(F,'mutate',[F,t1,'false','withdraw',[1,1]]),failure);
  await Promise.all(pages.map(p=>contains(p,'Nothing to take out')));
  const exact='SYNTHETIC PENDING — READ ONLY. No actual review or acknowledgment.\nOperation ID: false\nAction and payload: '+JSON.stringify(['withdraw',[1,1]],null,2)+'\nResult: '+JSON.stringify(failure,null,2);
  for(const p of pages) assert.equal(await p.locator('#spike-pending').textContent(),exact);
  const after=await (await fetch(base+'/api/state',{headers:{'x-spike-family':F,'x-spike-token':t1}})).json(); assert.equal(after.revision,before.revision);
  await connect(b,F,other); await contains(b,'No pending operation.');
  await b.locator('#spike-family').selectOption(G); await b.locator('#spike-token').fill(t2); await b.locator('#spike-connect').click();
  await b.waitForFunction(()=>document.querySelector('#spike-status').textContent==='access denied',null,{timeout:15000});
  await connect(b,F,t2); await contains(b,'Nothing to take out');
  await driver(F,'acknowledge',[F,t2,'false',failure]); await Promise.all(pages.map(p=>contains(p,'No pending operation.')));
  // Suppress one socket delivery on the first browser, then use visible resume.
  await a.evaluate(()=>{window.savedMessage=window.testSockets.at(-1).onmessage;window.testSockets.at(-1).onmessage=()=>{};});
  assert.deepEqual(await driver(F,'mutate',[F,t1,'lost','withdraw',[1,1]]),failure);
  await contains(b,'Nothing to take out'); await pause(300); await contains(a,'No pending operation.');
  await a.evaluate(()=>{window.testSockets.at(-1).onmessage=window.savedMessage;document.dispatchEvent(new Event('visibilitychange'));}); await contains(a,'Nothing to take out');
  // Suppress an acknowledgment invalidation; exercise the registered safety interval.
  await a.evaluate(()=>{window.savedMessage=window.testSockets.at(-1).onmessage;window.testSockets.at(-1).onmessage=()=>{};});
  await driver(F,'acknowledge',[F,t1,'lost',failure]); await contains(b,'No pending operation.');
  await pause(300); await contains(a,'Nothing to take out');
  await a.evaluate(()=>{window.testSockets.at(-1).onmessage=window.savedMessage;window.reconcile();}); await contains(a,'No pending operation.');
  assert.deepEqual(await driver(F,'mutate',[F,t1,'reload','withdraw',[1,1]]),failure); await contains(a,'Nothing to take out');
  await a.evaluate(()=>localStorage.clear()); await a.reload(); const fresh=await driver(F,'grant',[F]); await connect(a,F,fresh); await contains(a,'Nothing to take out');
  // A delayed old success must not restore a pending operation after a newer read.
  let releaseOld,seenOld,settleOld; const oldReached=new Promise(r=>seenOld=r), oldHeld=new Promise(r=>releaseOld=r), oldSettled=new Promise(r=>settleOld=r);
  const interceptOld=async route=>{const response=await route.fetch();seenOld();await oldHeld;await route.fulfill({response});settleOld();};
  await a.route('**/api/pending',interceptOld,{times:1});
  await a.evaluate(()=>document.dispatchEvent(new Event('visibilitychange'))); await oldReached;
  await driver(F,'acknowledge',[F,fresh,'reload',failure]); await contains(a,'No pending operation.');
  releaseOld(); await oldSettled; await pause(200); await contains(a,'No pending operation.');
  assert.deepEqual(await driver(F,'mutate',[F,fresh,'race','withdraw',[1,1]]),failure); await contains(a,'Nothing to take out');
  // Hold an actual pending response across logout, then release it.
  let release, seen, settled; const reached=new Promise(r=>seen=r), held=new Promise(r=>release=r), done=new Promise(r=>settled=r);
  await a.route('**/api/pending',async route=>{const response=await route.fetch(); seen(); await held; await route.fulfill({response});settled();},{times:1});
  await a.evaluate(()=>document.dispatchEvent(new Event('visibilitychange'))); await reached; await a.locator('#spike-logout').click(); release(); await done; await pause(200); await contains(a,'Disconnected; pending status unknown.');
  await a.unroute('**/api/pending'); await connect(a,F,fresh); await contains(a,'Nothing to take out');
  // Held response must not leak into a switched family.
  let release2,seen2,settled2; const reached2=new Promise(r=>seen2=r), held2=new Promise(r=>release2=r), done2=new Promise(r=>settled2=r);
  await a.route('**/api/pending',async route=>{const response=await route.fetch();seen2();await held2;await route.fulfill({response});settled2();},{times:1});
  await a.evaluate(()=>document.dispatchEvent(new Event('visibilitychange'))); await reached2; await connect(a,G,gt); await contains(a,'No pending operation.'); release2(); await done2; await pause(200); await contains(a,'No pending operation.'); await a.unroute('**/api/pending');
  // A failed pending response cannot masquerade as absence.
  await a.route('**/api/pending',route=>route.fulfill({status:500,body:'failed'})); await connect(a,F,fresh); await contains(a,'unable to retrieve'); await a.unroute('**/api/pending');
  await a.route('**/api/pending',route=>route.fulfill({status:200,body:'not json'})); await a.evaluate(()=>document.dispatchEvent(new Event('visibilitychange'))); await contains(a,'unable to retrieve'); await a.unroute('**/api/pending');
  // Strict response contract, independently exercised in both browser processes.
  const gift={operation_id:'id',request:['gift',[1,1,'note']],result:false,acknowledged:false};
  const zero={operation_id:'zero',request:['collect_rewards',[1,'all']],result:0,acknowledged:false};
  const invalid=[{}, {pending:null}, {...gift,operation_id:''}, {...gift,operation_id:'x'.repeat(201)},
   {...gift,operation_id:1}, {...gift,acknowledged:0}, {...gift,acknowledged:true},
   {...gift,extra:1}, {...gift,result:null}, {...gift,result:0}, {...gift,result:{}},
   {...gift,request:{}}, {...gift,request:['unknown',[]]}, {...gift,request:['gift',[true,1,'n']]},
   {...gift,request:['gift',[1,1.5,'n']]}, {...gift,request:['gift',[1,0,'n']]},
   {...gift,request:['gift',[1,1,null]]}, {...gift,request:['gift',[1,1,'n','extra']]},
   {...gift,request:['gift',[1,1,'n']],result:[false,'bad']},
   {...gift,request:['withdraw',[1,1]],result:[0,'bad']},
   {...gift,request:['withdraw',[1,1]],result:[false,{}]},
   {...gift,request:['withdraw',[1,1]],result:[false,'bad','extra']},
   {...zero,result:false}, {...zero,result:-1}, {...zero,result:1.5},
   {...zero,request:['collect_rewards',[1,'bogus']]},
   {...gift,request:['add_kid',['','x','y']],result:true},
   {...gift,request:['invest',[1,{},1]],result:[false,'bad']}];
  const noResult={...gift}; delete noResult.result; invalid.push(noResult);
  const noRequest={...gift}; delete noRequest.request; invalid.push(noRequest);
  for (const p of pages) {
   async function inject(body, expected) {
    await p.route('**/api/pending',route=>route.fulfill({status:200,contentType:'application/json',body}));
    const response=p.waitForResponse(r=>r.url().endsWith('/api/pending'));
    await p.evaluate(()=>window.reconcile()); await response; await contains(p,expected); await p.unroute('**/api/pending');
   }
   await inject('null','No pending operation.');
   await inject(JSON.stringify(gift),'Result: false');
   await inject(JSON.stringify(zero),'Result: 0');
   for (const value of invalid) { await inject(JSON.stringify(value),'unable to retrieve'); assert.ok(!(await p.locator('#spike-pending').textContent()).includes('No pending operation.')); }
   await inject('', 'unable to retrieve');
   // Undefined is not JSON, but guard against a broken response adapter returning it.
   await p.evaluate(()=>{const original=window.fetch;window.fetch=(...args)=>args[0]==='/api/pending' ? Promise.resolve({status:200,ok:true,json:async()=>undefined}) : original(...args); window.restoreFetch=()=>window.fetch=original;window.reconcile();});
   await contains(p,'unable to retrieve'); await p.evaluate(()=>window.restoreFetch());
  }
  // An old denial from a prior grant or family is ignored, but any current-context
  // denial remains terminal even after a newer read succeeded.
  for (const [family,token] of [[F,t2],[G,gt]]) {
   let releaseDenial, reachedDenial, settledDenial; const heldDenial=new Promise(r=>releaseDenial=r), reached=new Promise(r=>reachedDenial=r), settled=new Promise(r=>settledDenial=r);
   await a.route('**/api/pending',async route=>{reachedDenial();await heldDenial;await route.fulfill({status:403,body:'denied'});settledDenial();},{times:1});
   await a.evaluate(()=>window.reconcile()); await reached; await connect(a,family,token); await contains(a,family===G?'No pending operation.':'Nothing to take out');
   releaseDenial(); await settled; await pause(200); assert.equal(await a.locator('#spike-status').textContent(),'connected');
  }
  let releaseDenial, reachedDenial; const heldDenial=new Promise(r=>releaseDenial=r), reachedDenialPromise=new Promise(r=>reachedDenial=r);
  await a.route('**/api/pending',async route=>{reachedDenial();await heldDenial;await route.fulfill({status:403,body:'denied'});},{times:1});
  await a.evaluate(()=>window.reconcile()); await reachedDenialPromise;
  await a.evaluate(()=>window.reconcile()); await contains(a,'No pending operation.');
  releaseDenial(); await a.waitForFunction(()=>document.querySelector('#spike-status').textContent==='access denied'); await contains(a,'pending status unknown');
  // Exercise zero through the actual Python recovery result boundary; a false
  // gift result is valid at the bank contract but unreachable through current guards.
  await Promise.all(pages.map(p=>connect(p,G,gt))); await Promise.all(pages.map(p=>contains(p,'No pending operation.')));
  assert.equal(await driver(G,'mutate',[G,gt,'zero-seed','add_kid',['Seed']]),true);
  await Promise.all(pages.map(p=>contains(p,'Operation ID: zero-seed')));
  await driver(G,'acknowledge',[G,gt,'zero-seed',true]); await Promise.all(pages.map(p=>contains(p,'No pending operation.')));
  assert.equal(await driver(G,'mutate',[G,gt,'real-zero','collect_rewards',[1]]),0);
  await Promise.all(pages.map(p=>contains(p,'Result: 0')));
  await driver(G,'acknowledge',[G,gt,'real-zero',0]); await Promise.all(pages.map(p=>contains(p,'No pending operation.')));
  const full=await driver(F,'full_snapshot');
  for(const u of ['/api/pending','/api/acknowledge','/api/gift','/__unsafe_test_driver/call']) {const r=await fetch(base+u,{method:'POST',headers:{'content-type':'application/json','x-spike-family':F,'x-spike-token':fresh},body:'{}'});assert.equal(r.status,405);}
  assert.deepEqual(await driver(F,'full_snapshot'),full);
  await pause(200); const log=fs.readFileSync(process.env.WRANGLER_TEST_LOG,'utf8');
  for(const secret of [t1,t2,other,gt,fresh,process.env.TEST_SECRET]) assert.ok(!log.includes(secret),'fixture secret appeared in Wrangler request log');
  console.log('PASS two independent Chromium dashboards: pending, unchanged revision, scoped grants, denial, XSS, ack, lost push/resume, reload, races, unknown, blocked POST');
 } finally {await Promise.all(browsers.map(b=>b.close()));}
})().catch(e=>{console.error(e.stack);process.exit(1)});
