// UNSAFE SYNTHETIC LOOPBACK TEST FIXTURE. NEVER DEPLOY OR USE REAL DATA.
const familyOK = f => /^[ab]{32}$/.test(f || '');
export default {async fetch(request, env) {
  const u = new URL(request.url);
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(u.hostname)) return new Response('loopback only', {status:403});
  if (u.pathname.startsWith('/__unsafe_test_driver/')) {
    if (request.method !== 'POST' || !env.TEST_SECRET || request.headers.get('x-test-secret') !== env.TEST_SECRET)
      return new Response('unsafe test driver denied', {status:403});
    try {
      const b = await request.json();
      const allowed = ['setup','grant','mutate','acknowledge','fault','pending_sql_fault','full_snapshot'];
      if (!familyOK(b.family) || !allowed.includes(b.method) || !Array.isArray(b.args)) return new Response('invalid test request', {status:400});
      const stub = env.BANK.get(env.BANK.idFromName(b.family));
      const reply = await stub.fixture_call(b.method, b.args);
      return reply.ok ? Response.json(reply.value) : Response.json(reply.stage ? {error:reply.error,stage:reply.stage} : {error:reply.error}, {status:400});
    } catch (_) { return new Response('synthetic driver failed', {status:500}); }
  }
  // No dashboard write, including unknown routes, is forwarded to the DO.
  if (request.method !== 'GET') return new Response('read-only synthetic dashboard', {status:405});
  const socket = u.pathname === '/__spike/socket';
  const family = socket ? u.searchParams.get('family') : request.headers.get('x-spike-family');
  const token = socket ? u.searchParams.get('token') : request.headers.get('x-spike-token');
  if (!familyOK(family) || !token) return new Response('denied', {status:403});
  const stub = env.BANK.get(env.BANK.idFromName(family));
  try {
    if (socket) {
      const headers = new Headers(request.headers);
      headers.set('x-family', family); headers.set('x-token', token);
      return await stub.fetch(new Request(new URL('/socket', u), {headers}));
    }
    const kind = {'/api/state':'state','/api/transactions':'transactions','/api/pending':'pending'}[u.pathname];
    if (!kind) return new Response('not found', {status:404});
    const result = await stub.dashboard_read(family, token, kind, Number(u.searchParams.get('kid_id')));
    if (!result.ok) return new Response(result.denied ? 'denied' : 'synthetic request failed', {status:result.denied ? 403 : 500});
    return Response.json(result.value, {headers:{'cache-control':'no-store'}});
  } catch (_) { return new Response('synthetic request failed', {status:500}); }
}};
