// TEST ONLY: loopback dispatcher for actual JS Worker -> Python DO RPC.
// Deliberately unauthenticated and never deployable.
export default {
  async fetch(request, env) {
    if (request.method !== 'POST') return new Response('fixture', {status: 404});
    const body = await request.json();
    const stub = env.BANK.get(env.BANK.idFromName(body.object || 'synthetic'));
    try {
      let result;
      switch (body.method) {
        case 'setup': result = await stub.fixture_setup(...(body.args || [])); break;
        case 'echo': result = await stub.fixture_echo(body.value); break;
        case 'snapshot': result = await stub.fixture_snapshot(); break;
        case 'probe_snapshot': await stub.fixture_probe_snapshot(...body.args); result = null; break;
        case 'read': result = await stub.read(...body.args); break;
        case 'mutate': result = await stub.mutate(...body.args); break;
        case 'fault': result = await stub.fixture_fault(...body.args); break;
        case 'seed_allowance': await stub.fixture_seed_allowance(); result = null; break;
        case 'disable_allowance': await stub.fixture_disable_allowance(); result = null; break;
        case 'attempt': result = await stub[body.name](...body.args); break;
        default: throw new Error('unknown fixture method');
      }
      return Response.json({result});
    } catch (e) {
      // Categories are intentionally coarse: never return Python stack, exception
      // message, token, or other backend details to the requester.
      const text = String(e);
      const category = text.includes('PermissionError: denied') ? 'denied'
        : text.includes('does not exist') ? 'missing_method'
        : text.includes('TypeError:') ? 'bad_signature'
        : text.includes('synthetic injected fault') ? 'injected_fault'
        : text.includes('ValueError:') ? 'invalid_request' : 'rpc_error';
      // Expose only a boolean in this local diagnostic, never the raw exception.
      if (body.diagnose_traceback) return Response.json({error: category, raw_has_traceback: text.includes('Traceback')}, {status: 400});
      return Response.json({error: category}, {status: 400});
    }
  }
};
