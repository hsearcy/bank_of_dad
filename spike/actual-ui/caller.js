// NEVER DEPLOY: internal synthetic worker, reachable only via the local harness.
const validFamily = f => /^[ab]{32}$/.test(f || '');
export default {async fetch(request, env) {
  const u = new URL(request.url);
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(u.hostname)) return new Response('loopback only', {status:403});
  if (!u.pathname.startsWith('/__test/') && request.method !== 'GET') return new Response('read-only synthetic dashboard', {status:405});
  if (u.pathname === '/') {
    const asset = await env.ASSETS.fetch(new URL('/index.html', u));
    const html = (await asset.text()).replace('<script src="/static/app.js">', '<script src="/static/synthetic.js"></script><script src="/static/app.js">');
    return new Response(html, {headers:{'content-type':'text/html','cache-control':'no-store'}});
  }
  // Never serve the copied Python sources, state, or configuration as assets.
  if (/^\/static\/[\w.-]+$/.test(u.pathname) && u.pathname !== '/static/sw.js') return env.ASSETS.fetch(request);
  try {
    if (u.pathname.startsWith('/__test/')) {
      if (request.method !== 'POST' || !env.TEST_SECRET || request.headers.get('x-test-secret') !== env.TEST_SECRET) return new Response('test driver denied', {status:403});
      if (u.pathname === '/__test/setup') {
        const {family} = await request.json();
        if (!validFamily(family)) throw Error('family');
        return Response.json(await env.BANK.get(env.BANK.idFromName(family)).fixture_setup(family));
      }
      if (u.pathname === '/__test/snapshot') {
        const {family} = await request.json();
        if (!validFamily(family)) throw Error('family');
        return Response.json(await env.BANK.get(env.BANK.idFromName(family)).fixture_snapshot());
      }
      if (u.pathname === '/__test/mutate') {
        const {family,token,operation,action,args,dropNotification} = await request.json();
        if (!validFamily(family) || !token || !operation || !Array.isArray(args)) throw Error('test input');
        const result = await env.BANK.get(env.BANK.idFromName(family)).fixture_driver_mutate(family,token,operation,action,args,dropNotification === true);
        if (result.denied) return Response.json({ok:false,error:'access denied'},{status:403});
        return Response.json(result.value);
      }
      if (['/__test/clock','/__test/revoke','/__test/short-grant'].includes(u.pathname)) {
        const {family,token,seconds} = await request.json();
        if (!validFamily(family) || !token) throw Error('test input');
        const stub = env.BANK.get(env.BANK.idFromName(family));
        if (u.pathname.endsWith('/clock')) return Response.json(await stub.fixture_clock(family,token,seconds));
        if (u.pathname.endsWith('/short-grant')) return Response.json(await stub.fixture_short_grant(family,token));
        return Response.json(await stub.fixture_revoke(family,token));
      }
      return new Response('unknown test control', {status:404});
    }
    if (request.method !== 'GET') return new Response('read-only synthetic dashboard', {status:405});
    const family = u.pathname === '/__spike/socket' ? u.searchParams.get('family') : request.headers.get('x-spike-family');
    const token = u.pathname === '/__spike/socket' ? u.searchParams.get('token') : request.headers.get('x-spike-token');
    if (!validFamily(family) || !token) return Response.json({ok:false,error:'access denied'},{status:403});
    const stub = env.BANK.get(env.BANK.idFromName(family));
    if (u.pathname === '/__spike/socket') {
      const headers = new Headers(request.headers);
      headers.set('x-fixture-family', family); headers.set('x-fixture-token', token);
      return await stub.fetch(new Request(new URL('/socket',u), {headers}));
    }
    // Every read, including fake Dad status, must authorize at the Python DO.
    if (request.method === 'GET') {
      const kind = {'/api/state':'state','/api/transactions':'transactions','/api/admin/status':'admin'}[u.pathname];
      if (kind) {
        const result = await stub.fixture_api_read(family, token, kind, Number(u.searchParams.get('kid_id')));
        if (result.denied) return Response.json({ok:false,error:'access denied'},{status:403});
        return Response.json(result.value);
      }
    }
    return new Response('unsupported synthetic contract', {status:404});
  } catch(e) {return Response.json({ok:false,error:'synthetic request failed'},{status:500});}
}};
