/* Unsafe fake session controls, exclusively served by the local spike. */
(() => {
  let hooks, scope = null, epoch = 0, socket = null;
  const ui = document.createElement('aside');
  ui.style.cssText = 'position:fixed;bottom:0;right:0;z-index:200;background:white;max-width:440px;border:2px solid black;padding:4px';
  ui.innerHTML = `<b>READ-ONLY SYNTHETIC ONLY — fake grants, no real authentication</b>
    <select id="spike-family"><option value="${'a'.repeat(32)}">A</option><option value="${'b'.repeat(32)}">B</option></select>
    <input id="spike-token" placeholder="Fake scoped grant"><button id="spike-connect">Connect / reconnect</button>
    <button id="spike-logout">Logout</button><output id="spike-status">disconnected</output>`;
  document.body.prepend(ui);
  const style = document.createElement('style');
  style.textContent = '#dadBtn,.btn-spend,.btn-save,.btn-bond,.reward-btn {display:none !important}';
  document.head.append(style);
  const el = id => document.getElementById('spike-' + id);
  const status = value => {el('status').textContent = value;};
  function disconnect(clear = true) {
    epoch++;
    scope = null;
    const old = socket; socket = null;
    if (old) {old.onmessage = old.onopen = old.onclose = old.onerror = null; old.close();}
    hooks?.clear();
    if (clear) {el('token').value = '';}
    status('disconnected');
  }
  const stale = () => new DOMException('Synthetic context changed', 'AbortError');
  async function request(path, body) {
    const g = epoch, s = scope;
    if (!s) throw stale();
    if (body !== undefined) throw Error('Read-only synthetic dashboard');
    let response, result;
    try {
      response = await fetch(path, {method:'GET', cache:'no-store', headers:{'x-spike-family':s.family,'x-spike-token':s.token}});
      try { result = await response.json(); } catch (_) { result = null; }
    } catch (_) {
      if (g !== epoch || scope !== s) throw stale();
      throw Error('Synthetic request failed.');
    }
    if (g !== epoch || scope !== s) throw stale();
    if (!response.ok || !result || typeof result !== 'object' || Array.isArray(result)) throw Error('Synthetic request failed or unsupported.');
    return result;
  }
  const reload = async () => {
    const g = epoch, ws = socket;
    try {
      const applied = (await hooks.refresh()) !== false;
      if (applied && g === epoch && ws === socket && ws?.readyState === WebSocket.OPEN) {
        status('connected');
      }
      return applied;
    } catch (e) {if (e.name !== 'AbortError' && g === epoch) status('snapshot failed'); return false;}
  };
  async function connect(family, token) {
    disconnect(false);
    el('token').value = token;
    const g = epoch;
    scope = {family, token};
    const ws = new WebSocket(`ws://${location.host}/__spike/socket?family=${encodeURIComponent(family)}&token=${encodeURIComponent(token)}`);
    socket = ws;
    const current = () => g === epoch && socket === ws;
    ws.onmessage = e => {if (current() && e.data === '{"type":"invalidate"}') reload();};
    ws.onopen = () => {if (current()) reload();};
    ws.onclose = ws.onerror = () => {if (current()) disconnect(false);};
  }
  window.__BOD_SYNTHETIC_SPIKE__ = {enabled:true, request, context:()=>epoch,
    attach(value) {hooks = value;}, connect, disconnect};
  el('connect').onclick = () => connect(el('family').value, el('token').value);
  el('logout').onclick = () => disconnect();
  el('family').onchange = () => disconnect();
  document.addEventListener('visibilitychange', () => {if (scope && document.visibilityState === 'visible') reload();});
  // The actual UI has uncaught async handlers; surface fixture errors without a false success.
  window.addEventListener('unhandledrejection', e => {if (e.reason?.name !== 'AbortError') status(e.reason?.message || 'Synthetic request failed.'); e.preventDefault();});
})();