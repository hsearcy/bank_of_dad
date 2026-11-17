// Actual Flask app and disposable database, without the synthetic opt-in.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM || '/usr/bin/chromium',args:['--no-sandbox']});
 try {
  const p=await browser.newPage(); const errors=[]; p.on('pageerror',e=>errors.push(e.message));
  await p.addInitScript(()=>{
   const original=window.setInterval; window.testIntervals=[];
   window.setInterval=(fn,ms,...args)=>{window.testIntervals.push({fn,ms,args}); return original(fn,ms,...args);};
  });
  await p.goto(process.env.BASE_URL);
  assert.equal(await p.evaluate(()=>window.__BOD_SYNTHETIC_SPIKE__),undefined);
  const pin=async()=>{for(const digit of '1234') await p.locator(`.keypad [data-k="${digit}"]`).click();};
  await p.getByText('Set Dad PIN',{exact:false}).waitFor(); await pin();
  await p.getByText('Confirm PIN',{exact:false}).waitFor(); await pin();
  await p.waitForFunction(()=>!document.querySelector('.pin-card'));
  assert.equal((await (await p.request.get(process.env.BASE_URL+'/api/admin/status')).json()).pin_set,true);
  await p.locator('#dadBtn').click(); await pin();
  await p.locator('#dadPanel:not(.hidden)').waitFor();
  await p.locator('#newKidName').fill('Disposable child'); await p.locator('#addKidBtn').click();
  await p.locator('.name').filter({hasText:'Disposable child'}).waitFor();
  await p.locator('.gift-amt').fill('2'); await p.locator('.gift-note').fill('normal test');
  await p.locator('.gift-btn').click();
  await p.waitForFunction(()=>document.querySelector('.coins')?.textContent.includes('$7.00 to spend'));
  await p.locator('#closeDad').click(); await p.locator('.btn-save').click();
  await p.locator('.custom-amt').fill('1'); await p.locator('[data-act=save]').click();
  await p.waitForFunction(()=>document.querySelector('.s-val')?.textContent==='$1.00');
  // Invoke the exact registered polling callback; no 30s wall-clock dependency.
  assert.equal(await p.evaluate(()=>testIntervals.filter(x=>x.ms===30000).length),1);
  const response=p.waitForResponse(r=>r.url().endsWith('/api/state') && r.status()===200);
  await p.evaluate(()=>testIntervals.find(x=>x.ms===30000).fn()); await response;
  await p.waitForFunction(()=>navigator.serviceWorker.getRegistrations().then(rs=>rs.some(r=>r.active && r.active.scriptURL.endsWith('/sw.js'))));
  await p.locator('#dadBtn').click(); await p.locator('.pin-card').waitFor();
  await p.locator('.pin-card .close').click();
  assert.deepEqual(errors,[]);
  console.log('PASS: normal Flask browser: first-run PIN, Dad re-entry, add/gift/save, 30s poll callback, service worker.');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
