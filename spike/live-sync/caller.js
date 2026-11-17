// SYNTHETIC LOOPBACK FIXTURE ONLY: privileged test routes, never deploy.
const html = `<!doctype html><meta charset="utf-8"><title>Local synthetic live sync fixture</title>
<h1>Synthetic live sync — no real data</h1>
<label>Family <select id="family"><option value="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa">A</option><option value="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb">B</option></select></label>
<button id="setup">Bootstrap empty family (once)</button><p>Fixture grants: <output id="grants">none</output></p>
<label>Fixture grant <input id="token" size="50"></label><button id="connect">Connect</button>
<p>Wallet cents: <output id="balance">not connected</output></p><output id="status"></output>
<button id="gift">Gift 10 cents</button><script>
let family, token, socket; window.messages=[];
window.api=async(method, data={})=>{const r=await fetch('/api',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({method,...data})});const j=await r.json();if(!r.ok)throw Error(j.error);return j.result};
window.refresh=async()=>{const kids=await api('read',{family,token});document.querySelector('#balance').textContent=kids[0].wallet_cents;return kids};
window.connect=async(f,t)=>{family=f;token=t;await refresh();socket=new WebSocket('ws://'+location.host+'/socket?family='+encodeURIComponent(f)+'&token='+encodeURIComponent(t));window.socket=socket;socket.onmessage=async e=>{messages.push(e.data);await refresh()};await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=reject});await refresh()};
window.disconnect=()=>socket.close();
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible' && token) refresh().catch(()=>{});});
window.gift=async(op,cents=10,fault=false)=>api('gift',{family,token,op,cents,fault});
document.querySelector('#setup').onclick=async()=>{try {const grants=await api('setup',{family:document.querySelector('#family').value});document.querySelector('#grants').textContent=grants.join(' | ');document.querySelector('#token').value=grants[0];}catch(e){document.querySelector('#status').textContent=e.message}};
document.querySelector('#connect').onclick=async()=>{try {await connect(document.querySelector('#family').value,document.querySelector('#token').value);document.querySelector('#status').textContent='connected'}catch(e){document.querySelector('#status').textContent='denied'}};
document.querySelector('#gift').onclick=()=>gift(crypto.randomUUID()).catch(e=>document.querySelector('#status').textContent=e.message);
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
      case 'gift': result=await stub.fixture_gift(b.family,b.token,b.op,b.cents,!!b.fault);break;
      case 'revoke': result=await stub.fixture_revoke(b.family,b.token);break;
      case 'expire': result=await stub.fixture_expire(b.family,b.token);break;
      default: throw Error('invalid method');
    }
    return Response.json({result});
  } catch(e) {return Response.json({error:'fixture request denied or failed'},{status:400});}
}};
