// SYNTHETIC LOOPBACK TEST DRIVER ONLY. Never deploy.
export default { async fetch(request, env) {
  const b = await request.json();
  const stub = env.COMBINED.get(env.COMBINED.idFromName(b.object || 'family'));
  if (b.method === 'raw') {
    try {
      await stub[b.args[0]](...b.args.slice(1));
      return Response.json({result:'unexpected-success'});
    } catch (e) {
      // This tests RPC exposure only, not a domain denial category.
      const expected = `TypeError: Method ${b.args[0]} does not exist`;
      return Response.json({error:String(e) === expected ? 'raw-unavailable' : 'unexpected', detail:String(e)}, {status:400});
    }
  }
  if (b.method === 'disconnect') {
    const reply = await stub.fixture_call('mutate', b.args);
    if (!reply.ok) return Response.json(reply, {status:400});
    // Commit and RPC return have occurred; hold the HTTP response until released.
    await stub.fixture_gate('committed');
    while (await stub.fixture_gate() !== 'release') {
      await new Promise(resolve => setTimeout(resolve, 25));
    }
    await stub.fixture_gate('resumed');
    return Response.json({result:reply.value});
  }
  const reply = await stub.fixture_call(b.method, b.args);
  return reply.ok ? Response.json({result:reply.value}) : Response.json(reply, {status:400});
}};
