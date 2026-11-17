// SYNTHETIC LOOPBACK ONLY. Privileged setup is deliberately unauthenticated.
const validFamily = f => /^[ab]{32}$/.test(f || '');
export default {async fetch(request, env) {
  const u = new URL(request.url);
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(u.hostname)) return new Response('loopback only', {status:403});
  if (u.pathname === '/') {
    const asset = await env.ASSETS.fetch(new URL('/index.html', u));
    const html = (await asset.text()).replace('<script src="/static/app.js">', '<script src="/static/synthetic.js"></script><script src="/static/app.js">');
    return new Response(html, {headers:{'content-type':'text/html','cache-control':'no-store'}});
  }
  // Never serve the copied Python sources, state, or configuration as assets.
  if (/^\/static\/[\w.-]+$/.test(u.pathname) && u.pathname !== '/static/sw.js') return env.ASSETS.fetch(request);
  try {
    if (u.pathname === '/__spike/setup' && request.method === 'POST') {
      const {family} = await request.json();
      if (!validFamily(family)) throw Error('family');
      return Response.json(await env.BANK.get(env.BANK.idFromName(family)).fixture_setup(family));
    }
    const family = u.pathname === '/__spike/socket' ? u.searchParams.get('family') : request.headers.get('x-spike-family');
    const token = u.pathname === '/__spike/socket' ? u.searchParams.get('token') : request.headers.get('x-spike-token');
    if (!validFamily(family) || !token) throw Error('scope');
    const stub = env.BANK.get(env.BANK.idFromName(family));
    if (u.pathname === '/__spike/socket') {
      const headers = new Headers(request.headers);
      headers.set('x-fixture-family', family); headers.set('x-fixture-token', token);
      return stub.fetch(new Request(new URL('/socket',u), {headers}));
    }
    // Every read, including fake Dad status, must authorize at the Python DO.
    if (request.method === 'GET') {
      if (u.pathname === '/api/state') return Response.json(await stub.fixture_dashboard(family,token));
      if (u.pathname === '/api/transactions') return Response.json(await stub.fixture_transactions(family,token,Number(u.searchParams.get('kid_id'))));
      if (u.pathname === '/api/admin/status') {
        await stub.read(family,token,'kids',[]);
        return Response.json({pin_set:false}); // fixture bypass only, no real PIN claim
      }
    }
    if (request.method === 'POST') {
      const b = await request.json();
      const routes = {
        '/api/admin/gift':['gift',[b.kid_id,b.amount_cents,b.note ?? b.reason ?? '']],
        '/api/save':['save',[b.kid_id,b.amount_cents]],
        '/api/spend':['spend',[b.kid_id,b.amount_cents,b.reason ?? '']],
      };
      if (routes[u.pathname]) {
        const [action,args] = routes[u.pathname];
        const result = await stub.fixture_mutate(family,token,crypto.randomUUID(),action,args);
        return Response.json(action === 'gift' ? {ok:result} : {ok:result[0],message:result[1]});
      }
    }
    return new Response('unsupported synthetic contract', {status:404});
  } catch(e) {return Response.json({ok:false,error:'synthetic request denied or failed'},{status:403});}
}};
