/* Unsafe fake session controls, exclusively served by the local spike. */
(() => {
  let hooks, scope = null, epoch = 0, socket = null;
  const ui = document.createElement('aside');
  ui.style.cssText = 'position:fixed;bottom:0;right:0;z-index:200;background:white;max-width:440px;border:2px solid black;padding:4px';
  ui.innerHTML = `<b>SYNTHETIC ONLY — fake grants, no real authentication</b>
    <select id="spike-family"><option value="${'a'.repeat(32)}">A</option><option value="${'b'.repeat(32)}">B</option></select>
    <button id="spike-setup">Bootstrap once</button><output id="spike-grants"></output>
    <input id="spike-token" placeholder="Fake scoped grant"><button id="spike-connect">Connect / reconnect</button>
    <button id="spike-logout">Logout</button><output id="spike-status">disconnected</output>`;
  document.body.prepend(ui);
  const el = id => document.getElementById('spike-' + id);
  const status = value => {el('status').textContent = value;};
  function disconnect(clear = true) {
    epoch++;
    scope = null;
    const old = socket; socket = null;
    if (old) {old.onmessage = old.onopen = old.onclose = old.onerror = null; old.close();}
    hooks?.clear();
    if (clear) {el('token').value = ''; el('grants').textContent = '';}
    status('disconnected');
  }
  const stale = () => new DOMException('Synthetic context changed', 'AbortError');
  async function request(path, body) {
    const g = epoch, s = scope;
    if (!s) throw stale();
    const response = await fetch(path, {method:body ? 'POST':'GET', cache:'no-store',
      headers:{'content-type':'application/json','x-spike-family':s.family,'x-spike-token':s.token},
      body:body ? JSON.stringify(body):undefined});
    const result = await response.json();
    // Applies to reads AND mutation completions before any app continuation.
    if (g !== epoch || scope !== s) throw stale();
    if (!response.ok) {disconnect(false); throw Error(result.error);}
    return result;
  }
  const reload = () => hooks.refresh().catch(e => {if (e.name !== 'AbortError') status('denied');});
  async function connect(family, token) {
    disconnect(false);
    el('token').value = token;
    const g = epoch;
    scope = {family, token};
    const ws = new WebSocket(`ws://${location.host}/__spike/socket?family=${encodeURIComponent(family)}&token=${encodeURIComponent(token)}`);
    socket = ws;
    const current = () => g === epoch && socket === ws;
    ws.onmessage = e => {if (current() && e.data === '{"type":"invalidate"}') reload();};
    ws.onopen = async () => {if (current()) {await reload(); if (current()) status('connected');}};
    ws.onclose = ws.onerror = () => {if (current()) disconnect(false);};
  }
  window.__BOD_SYNTHETIC_SPIKE__ = {enabled:true, request, context:()=>epoch,
    attach(value) {hooks = value;}, connect, disconnect};
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
  // Stale async UI handlers stop at api(); suppress only the intentional abort.
  window.addEventListener('unhandledrejection', e => {if (e.reason?.name === 'AbortError') e.preventDefault();});
})();
