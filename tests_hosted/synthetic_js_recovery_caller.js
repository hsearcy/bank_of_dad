// LOCAL TEST ONLY. Unauthenticated synthetic fixture; never deploy.
export default { async fetch(request, env) {
  const b = await request.json();
  const stub = env.RECOVERY.get(env.RECOVERY.idFromName(b.object || 'family'));
  try {
    let result;
    if (b.method === 'setup') result = await stub.fixture_setup(...b.args);
    else if (b.method === 'v1_read') result = await stub.fixture_v1_read(...b.args);
    else if (b.method === 'fault') result = await stub.fixture_fault(...b.args);
    else result = await stub.fixture_invoke(b.method, b.args);
    return Response.json({result});
  } catch (e) {
    const s = String(e);
    const category = s.includes('PermissionError:') ? 'denied' : s.includes('ValueError:') ? 'invalid' : s.includes('synthetic injected fault') ? 'fault' : s.includes('RuntimeError:') ? 'runtime' : 'other';
    return Response.json({error:category}, {status:400});
  }
}};
