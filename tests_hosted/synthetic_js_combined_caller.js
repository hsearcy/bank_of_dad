// SYNTHETIC LOOPBACK TEST DRIVER ONLY. Never deploy.
export default { async fetch(request, env) {
  const b = await request.json();
  const stub = env.COMBINED.get(env.COMBINED.idFromName(b.object || 'family'));
  try {
    let result;
    if (b.method === 'drop') {
      // Model a committed reply lost at the synthetic gateway.
      await stub.mutate(...b.args);
      result = null;
    } else if (b.method === 'raw') {
      // Exercise the actual JS RPC boundary, not the protected dispatcher.
      result = await stub[b.args[0]](...b.args.slice(1));
    } else {
      const methods = {read:'read', snapshot:'snapshot', mutate:'mutate', pending:'pending',
        outcome:'outcome', acknowledge:'acknowledge', setup:'fixture_setup',
        full:'fixture_full_snapshot', fault:'fixture_fault', sql:'fixture_sql',
        revoke:'fixture_revoke', expire:'fixture_expire'};
      if (!Object.hasOwn(methods,b.method)) throw new Error('unknown test method');
      result = await stub[methods[b.method]](...b.args);
    }
    return Response.json({result});
  } catch (e) {
    const s = String(e);
    const category = s.includes('PermissionError:') ? 'denied' : s.includes('ValueError:') ? 'invalid' : s.includes('synthetic injected fault') ? 'fault' : s.includes('RuntimeError:') ? 'runtime' : 'other';
    return Response.json({error:category}, {status:400});
  }
}};
