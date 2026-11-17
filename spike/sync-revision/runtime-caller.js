// Local test harness only; never deploy.
export default {async fetch(request, env) {
  const u = new URL(request.url);
  const obj = env.BANK.get(env.BANK.idFromName(u.searchParams.get('object') || 'a'.repeat(32)));
  if (u.pathname === '/socket') {
    const h = new Headers(request.headers);
    h.set('x-fixture-family', u.searchParams.get('family'));
    h.set('x-fixture-token', u.searchParams.get('token'));
    return obj.fetch(new Request(request.url, {headers:h}));
  }
  try {
    const args = request.method === 'POST' ? await request.json() : [];
    const action = u.pathname.slice(1);
    if (!['fixture_setup','fixture_snapshot','fixture_dashboard','fixture_transactions','fixture_mutate','fixture_fault','fixture_bootstrap_fault','fixture_corrupt','fixture_break_schema','fixture_revoke','mutate','read'].includes(action)) throw Error('unknown');
    const result = await obj[action](...args);
    return Response.json({result});
  } catch(e) {return Response.json({error:String(e)}, {status:403});}
}};
