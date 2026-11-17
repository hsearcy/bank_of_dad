/* Unsafe fake session controls, exclusively served by the local spike. */
(() => {
  let hooks, scope = null, epoch = 0, socket = null, inFlight = false, snapshotReady = false;
  const pendingKey = 'bod-synthetic-pending-v1';
  const mutations = new Set(['/api/admin/gift', '/api/save', '/api/spend']);
  const ui = document.createElement('aside');
  ui.style.cssText = 'position:fixed;bottom:0;right:0;z-index:200;background:white;max-width:440px;border:2px solid black;padding:4px';
  ui.innerHTML = `<b>SYNTHETIC ONLY — fake grants, no real authentication</b>
    <select id="spike-family"><option value="${'a'.repeat(32)}">A</option><option value="${'b'.repeat(32)}">B</option></select>
    <button id="spike-setup">Bootstrap once</button><output id="spike-grants"></output>
    <input id="spike-token" placeholder="Fake scoped grant"><button id="spike-connect">Connect / reconnect</button>
    <button id="spike-logout">Logout</button><button id="spike-retry" hidden>Retry unresolved operation</button><output id="spike-status">disconnected</output>`;
  document.body.prepend(ui);
  const el = id => document.getElementById('spike-' + id);
  const status = value => {el('status').textContent = value;};
  const pending = () => JSON.parse(sessionStorage.getItem(pendingKey) || 'null');
  const showPending = () => {el('retry').hidden = !scope || !pending();};
  const clearPending = () => {
    sessionStorage.removeItem(pendingKey); showPending();
    if (scope && snapshotReady && socket?.readyState === WebSocket.OPEN && el('status').textContent.startsWith('Outcome unresolved')) status('connected');
  };
  function disconnect(clear = true) {
    epoch++;
    scope = null; snapshotReady = false;
    const old = socket; socket = null;
    if (old) {old.onmessage = old.onopen = old.onclose = old.onerror = null; old.close();}
    hooks?.clear();
    if (clear) {clearPending(); el('token').value = ''; el('grants').textContent = '';}
    showPending(); status('disconnected');
  }
  const stale = () => new DOMException('Synthetic context changed', 'AbortError');
  async function send(path, bodyText, operation, g, s) {
    let response, result;
    try {
      response = await fetch(path, {method:bodyText ? 'POST':'GET', cache:'no-store',
        headers:{'content-type':'application/json','x-spike-family':s.family,'x-spike-token':s.token,
          ...(operation ? {'x-spike-operation':operation.id} : {})}, body:bodyText || undefined});
      try { result = await response.json(); } catch (_) { result = null; }
    } catch (_) {
      if (g !== epoch || scope !== s) throw stale();
      throw Error(operation ? 'Outcome unresolved; retry the same operation.' : 'Synthetic request failed.');
    }
    if (g !== epoch || scope !== s) throw stale();
    if (!response.ok || !result || typeof result !== 'object' || Array.isArray(result) ||
        (operation && typeof result.ok !== 'boolean'))
      throw Error(operation ? 'Outcome unresolved; retry the same operation.' : 'Synthetic request failed or unsupported.');
    if (operation) {
      clearPending();
      if (!result.ok) throw Error('Synthetic operation was rejected.');
    }
    return result;
  }
  async function request(path, body) {
    const g = epoch, s = scope;
    if (!s) throw stale();
    const isMutation = !!body && mutations.has(path);
    let operation = null;
    if (isMutation) {
      if (pending() || inFlight) throw Error('Unresolved operation: use the retry button before another mutation.');
      operation = {family:s.family, principal:s.principal, id:crypto.randomUUID(), path, body:JSON.stringify(body)};
      // If storage is unavailable, fail closed before sending anything.
      sessionStorage.setItem(pendingKey, JSON.stringify(operation));
      showPending();
      inFlight = true;
    }
    try { return await send(path, isMutation ? operation.body : body ? JSON.stringify(body) : null, operation, g, s); }
    finally {if (isMutation) inFlight = false;}
  }
  const reload = async () => {
    const g = epoch, ws = socket;
    try {
      const applied = (await hooks.refresh()) !== false;
      if (applied && g === epoch && ws === socket && ws?.readyState === WebSocket.OPEN) {
        snapshotReady = true;
        status(pending() ? 'Outcome unresolved; retry the same operation.' : 'connected');
      }
      return applied;
    } catch (e) {if (e.name !== 'AbortError' && g === epoch) status(pending() ? 'Snapshot failed; outcome unresolved.' : 'snapshot failed'); return false;}
  };
  async function connect(family, token) {
    disconnect(false);
    el('token').value = token;
    const g = epoch;
    // A grant fingerprint scopes the tab record without storing the grant itself.
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(token));
    if (g !== epoch) return;
    const principal = Array.from(new Uint8Array(digest), x => x.toString(16).padStart(2,'0')).join('');
    const saved = pending();
    if (saved && (saved.family !== family || saved.principal !== principal)) clearPending();
    scope = {family, token, principal}; showPending();
    const ws = new WebSocket(`ws://${location.host}/__spike/socket?family=${encodeURIComponent(family)}&token=${encodeURIComponent(token)}`);
    socket = ws;
    const current = () => g === epoch && socket === ws;
    ws.onmessage = e => {if (current() && e.data === '{"type":"invalidate"}') reload();};
    ws.onopen = () => {if (current()) reload();};
    ws.onclose = ws.onerror = () => {if (current()) disconnect(false);};
  }
  window.__BOD_SYNTHETIC_SPIKE__ = {enabled:true, request, context:()=>epoch,
    attach(value) {hooks = value;}, connect, disconnect};
  el('retry').onclick = async () => {
    const operation = pending(), s = scope, g = epoch;
    if (!operation || !s || inFlight) return;
    inFlight = true;
    try {
      await send(operation.path, operation.body, operation, g, s);
      await reload();
    } catch (e) {if (e.name !== 'AbortError') status(e.message);}
    finally {inFlight = false;}
  };
  el('setup').onclick = async () => {
    const g = epoch, family = el('family').value;
    const r = await fetch('/__spike/setup', {method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({family})});
    const grants = await r.json();
    if (g !== epoch) return;
    if (!r.ok) {status('setup failed'); return;}
    el('grants').textContent = grants.join(' | '); el('token').value = grants[0];
  };
  el('connect').onclick = () => connect(el('family').value, el('token').value);
  el('logout').onclick = () => disconnect();
  el('family').onchange = () => disconnect();
  document.addEventListener('visibilitychange', () => {if (scope && document.visibilityState === 'visible') reload();});
  // The actual UI has uncaught async handlers; surface fixture errors without a false success.
  window.addEventListener('unhandledrejection', e => {if (e.reason?.name !== 'AbortError') status(e.reason?.message || 'Synthetic request failed.'); e.preventDefault();});
})();