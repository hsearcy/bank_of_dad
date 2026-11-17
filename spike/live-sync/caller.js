// SYNTHETIC LOOPBACK FIXTURE ONLY: privileged test routes, never deploy.
const html = `<!doctype html><meta charset="utf-8"><title>Local synthetic live sync fixture</title>
<h1>Synthetic live sync — no real data</h1>
<label>Family <select id="family"><option value="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa">A</option><option value="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb">B</option></select></label>
<button id="setup">Bootstrap empty family (once)</button><p>Fixture grants: <output id="grants">none</output></p>
<label>Fixture grant <input id="token" size="50"></label><button id="connect">Connect</button>
<p>Wallet cents: <output id="balance">not connected</output></p><output id="status"></output>
<button id="gift">Gift 10 cents</button><button id="logout">Logout</button><script>
let family, token, socket, lastFamily, lastToken, generation=0, refreshGeneration=0, setupGeneration=0, mutationGeneration=0, cancelConnect; window.messages=[]; window.refreshes=[];
window.api=async(method, data={})=>{const r=await fetch('/api',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({method,...data})});const j=await r.json();if(!r.ok)throw Error(j.error);return j.result};
window.refresh=async()=>{if(!token)return;const g=generation, n=++refreshGeneration, f=family, t=token;window.refreshes.push(f);try {const kids=await api('read',{family:f,token:t});if(g!==generation || n!==refreshGeneration)return;document.querySelector('#balance').textContent=kids[0].wallet_cents;return kids}catch(e){if(g===generation && n===refreshGeneration)throw e}};
window.disconnect=(clearCredentials=true)=>{generation++;refreshGeneration++;setupGeneration++;const cancel=cancelConnect;cancelConnect=null;if(cancel)cancel();const old=socket;socket=null;window.socket=null;if(old){old.onmessage=old.onopen=old.onerror=old.onclose=null;old.close()}family=token=null;if(clearCredentials){lastFamily=lastToken=null;document.querySelector('#token').value='';document.querySelector('#grants').textContent='none'}document.querySelector('#balance').textContent='not connected';document.querySelector('#status').textContent='disconnected'};
window.connect=async(f,t)=>{disconnect(!!lastFamily && (lastFamily!==f || lastToken!==t));lastFamily=family=f;lastToken=token=t;const g=generation;try {await refresh();if(g!==generation)return;const ws=new WebSocket('ws://'+location.host+'/socket?family='+encodeURIComponent(f)+'&token='+encodeURIComponent(t));socket=ws;window.socket=ws;const current=()=>g===generation && socket===ws;ws.onmessage=e=>{if(!current())return;messages.push(e.data);refresh().catch(()=>{})};await new Promise((resolve,reject)=>{const cancel=()=>reject(Error('connection cancelled'));cancelConnect=cancel;ws.onopen=()=>{if(!current() || cancelConnect!==cancel)return;cancelConnect=null;resolve()};const lost=()=>{if(current())disconnect(false)};ws.onerror=lost;ws.onclose=lost});if(current())await refresh()}catch(e){if(g===generation)throw e}};
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible' && token) refresh().catch(()=>{});});
window.gift=async(op,cents=10,fault=false)=>{const g=generation,f=family,t=token;++mutationGeneration;try {const result=await api('gift',{family:f,token:t,op,cents,fault});if(g===generation)return result}catch(e){if(g===generation)throw e}};
document.querySelector('#family').onchange=()=>disconnect();
document.querySelector('#setup').onclick=async()=>{const g=generation,n=++setupGeneration,f=document.querySelector('#family').value;try {const grants=await api('setup',{family:f});if(g!==generation || n!==setupGeneration)return;document.querySelector('#grants').textContent=grants.join(' | ');document.querySelector('#token').value=grants[0];}catch(e){if(g===generation && n===setupGeneration)document.querySelector('#status').textContent=e.message}};
document.querySelector('#connect').onclick=async()=>{const pending=connect(document.querySelector('#family').value,document.querySelector('#token').value), g=generation;try {await pending;if(g===generation)document.querySelector('#status').textContent='connected'}catch(e){if(g===generation)document.querySelector('#status').textContent='denied'}};
document.querySelector('#gift').onclick=()=>{const g=generation,pending=gift(crypto.randomUUID()),n=mutationGeneration;pending.catch(e=>{if(g===generation && n===mutationGeneration)document.querySelector('#status').textContent=e.message})};
document.querySelector('#logout').onclick=()=>disconnect();
</script>`;
export default {async fetch(request,env){
  const u=new URL(request.url);
  if(u.pathname==='/') return new Response(html,{headers:{'content-type':'text/html'}});
  if(u.pathname==='/socket'){
    const family=u.searchParams.get('family'), token=u.searchParams.get('token');
    const h=new Headers(request.headers);h.set('x-fixture-family',family);h.set('x-fixture-token',token);
    const clean=new URL(request.url);clean.search='';
    return env.BANK.get(env.BANK.idFromName(family)).fetch(new Request(clean, {method:'GET',headers:h}));
  }
  if(u.pathname!=='/api'||request.method!=='POST') return new Response('not found',{status:404});
  try {
    const b=await request.json(); if(!/^[ab]{32}$/.test(b.family)) throw Error('invalid family');
    const stub=env.BANK.get(env.BANK.idFromName(b.family)); let result;
    switch(b.method){
      case 'setup': result=await stub.fixture_setup(b.family);break;
      case 'read': result=await stub.read(b.family,b.token,b.history ? 'transactions' : 'kids',b.history ? [1] : []);break;
      case 'gift': result=await stub.fixture_mutate(b.family,b.token,b.op,'gift',[1,b.cents,'synthetic'],!!b.fault);break;
      case 'mutate': result=await stub.fixture_mutate(b.family,b.token,b.op,b.action,b.args,!!b.fault);break;
      case 'revoke': result=await stub.fixture_revoke(b.family,b.token);break;
      case 'expire': result=await stub.fixture_expire(b.family,b.token);break;
      case 'allowance_due': result=await stub.fixture_allowance_due(b.family,b.token);break;
      case 'mature_zero': result=await stub.fixture_mature_zero(b.family,b.token);break;
      default: throw Error('invalid method');
    }
    return Response.json({result});
  } catch(e) {return Response.json({error:'fixture request denied or failed'},{status:400});}
}};
