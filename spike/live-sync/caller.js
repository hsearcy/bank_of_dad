// SYNTHETIC LOOPBACK FIXTURE ONLY: privileged test routes, never deploy.
const html = `<!doctype html><meta charset="utf-8"><title>Local synthetic live sync fixture</title>
<h1>Synthetic live sync — no real data</h1>
<label>Family <select id="family"><option value="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa">A</option><option value="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb">B</option></select></label>
<button id="setup">Bootstrap empty family (once)</button><p>Fixture grants: <output id="grants">none</output></p>
<label>Fixture grant <input id="token" size="50"></label><button id="connect">Connect</button>
<p>Wallet cents: <output id="balance">not connected</output></p><output id="status"></output>
<button id="gift">Gift 10 cents</button><button id="logout">Logout</button><script>
let family, token, socket, generation=0, refreshGeneration=0, cancelConnect; window.messages=[]; window.refreshes=[];
window.api=async(method, data={})=>{const r=await fetch('/api',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({method,...data})});const j=await r.json();if(!r.ok)throw Error(j.error);return j.result};
window.refresh=async()=>{if(!token)return;const g=generation, n=++refreshGeneration, f=family, t=token;window.refreshes.push(f);const kids=await api('read',{family:f,token:t});if(g===generation && n===refreshGeneration)document.querySelector('#balance').textContent=kids[0].wallet_cents;return kids};
window.disconnect=()=>{generation++;refreshGeneration++;if(cancelConnect){cancelConnect();cancelConnect=null}if(socket){socket.onmessage=socket.onopen=socket.onerror=socket.onclose=null;socket.close();socket=null}family=token=null;document.querySelector('#balance').textContent='not connected';document.querySelector('#status').textContent='disconnected'};
window.connect=async(f,t)=>{disconnect();family=f;token=t;const g=generation;await refresh();if(g!==generation)return;const ws=new WebSocket('ws://'+location.host+'/socket?family='+encodeURIComponent(f)+'&token='+encodeURIComponent(t));socket=ws;window.socket=ws;ws.onmessage=e=>{if(g!==generation || socket!==ws)return;messages.push(e.data);refresh().catch(()=>{})};await new Promise((resolve,reject)=>{cancelConnect=()=>reject(Error('connection cancelled'));ws.onopen=()=>{cancelConnect=null;resolve()};ws.onerror=()=>{cancelConnect=null;reject(Error('connection failed'))};ws.onclose=()=>{cancelConnect=null;reject(Error('connection closed'))}});if(g===generation)await refresh()};
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible' && token) refresh().catch(()=>{});});
window.gift=async(op,cents=10,fault=false)=>api('gift',{family,token,op,cents,fault});
document.querySelector('#setup').onclick=async()=>{try {const grants=await api('setup',{family:document.querySelector('#family').value});document.querySelector('#grants').textContent=grants.join(' | ');document.querySelector('#token').value=grants[0];}catch(e){document.querySelector('#status').textContent=e.message}};
document.querySelector('#connect').onclick=async()=>{const pending=connect(document.querySelector('#family').value,document.querySelector('#token').value), g=generation;try {await pending;if(g===generation)document.querySelector('#status').textContent='connected'}catch(e){if(g===generation)document.querySelector('#status').textContent='denied'}};
document.querySelector('#gift').onclick=()=>gift(crypto.randomUUID()).catch(e=>document.querySelector('#status').textContent=e.message);
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
