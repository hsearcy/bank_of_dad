// UNSAFE LOOPBACK TEST DRIVER: privileged controls, fake credentials. NEVER DEPLOY.
export default {async fetch(request, env) {
  const url = new URL(request.url);
  if (url.pathname === '/socket') {
    const stub = env.COMBINED.get(env.COMBINED.idFromName(url.searchParams.get('object')));
    const headers = new Headers(request.headers);
    headers.set('x-family', url.searchParams.get('family'));
    headers.set('x-token', url.searchParams.get('token'));
    return stub.fetch(new Request(request, {headers}));
  }
  const b = await request.json();
  const stub = env.COMBINED.get(env.COMBINED.idFromName(b.object || 'family'));
  if (b.method === 'raw') {
    try { await stub[b.args[0]](...b.args.slice(1)); return Response.json({result:'unexpected-success'}); }
    catch(e) { return Response.json({error:String(e)}, {status:400}); }
  }
  if (b.method === 'gate') return Response.json({result:await stub.fixture_gate(...b.args)});
  const reply = await stub.fixture_call(b.method === 'disconnect' ? 'mutate' : b.method, b.args);
  if (b.method === 'disconnect' && reply.ok) {
    await stub.fixture_gate('committed');
    while (await stub.fixture_gate() !== 'release') await new Promise(r=>setTimeout(r,25));
    await stub.fixture_gate('resumed');
  }
  return reply.ok ? Response.json({result:reply.value}) : Response.json({error:reply.error},{status:400});
}};
