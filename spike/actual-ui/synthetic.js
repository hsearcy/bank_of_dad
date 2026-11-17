/* Unsafe fake session controls, exclusively served by the local spike. */
(() => {
  let hooks, scope = null, epoch = 0, socket = null, revision = -1;
  let retryTimer = null, safetyTimer = null, attempts = 0, acceptedSerial = 0, preflight = null;
  // Test harness may accelerate this synthetic-only safety net.
  const safetyMs = Number(window.__BOD_SYNTHETIC_SAFETY_MS__) || 60000;
  const clearTimers = () => {clearTimeout(retryTimer); clearInterval(safetyTimer); retryTimer = safetyTimer = null; preflight?.abort(); preflight = null;};
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
    clearTimers(); attempts = 0; revision = -1; acceptedSerial = 0;
    scope = null;
    const old = socket; socket = null;
    if (old) {old.onmessage = old.onopen = old.onclose = old.onerror = null; old.close();}
    hooks?.clear();
    if (clear) {el('token').value = '';}
    status('disconnected');
  }
  const stale = () => new DOMException('Synthetic context changed', 'AbortError');
  async function request(path, body, signal) {
    const g = epoch, s = scope;
    if (!s) throw stale();
    if (body !== undefined) throw Error('Read-only synthetic dashboard');
    let response, result;
    try {
      response = await fetch(path, {method:'GET', cache:'no-store', signal, headers:{'x-spike-family':s.family,'x-spike-token':s.token}});
    } catch (_) {
      if (g !== epoch || scope !== s) throw stale();
      throw Error('Synthetic request failed.');
    }
    if (g !== epoch || scope !== s) throw stale();
    if (response.status === 401 || response.status === 403) {
      const e = Error('Synthetic access denied'); e.denied = true;
      disconnect(); status('access denied');
      throw e;
    }
    try { result = await response.json(); } catch (_) { result = null; }
    if (g !== epoch || scope !== s) throw stale();
    if (!response.ok || !result || typeof result !== 'object' || Array.isArray(result)) throw Error('Synthetic request failed or unsupported.');
    return result;
  }
  const reload = async () => {
    const g = epoch, ws = socket;
    try {
      const applied = (await hooks.refresh()) !== false;
      if (applied && g === epoch && ws === socket && ws?.readyState === WebSocket.OPEN) {
        attempts = 0;
        status('connected');
      }
      return applied;
    } catch (e) {
      if (e.name !== 'AbortError' && g === epoch) {
        if (e.denied) {disconnect(); status('access denied');}
        else status('snapshot failed');
      }
      return false;
    }
  };
  function schedule() {
    if (!scope || retryTimer || preflight || !navigator.onLine) return;
    const g = epoch, delay = Math.min(30000, 500 * 2 ** Math.min(attempts++, 6));
    retryTimer = setTimeout(async () => {
      retryTimer = null;
      if (g !== epoch || !scope) return;
      // A denied read is terminal even when the socket handshake cannot report why.
      const controller = new AbortController(); preflight = controller;
      const timeout = setTimeout(() => controller.abort(), 5000);
      try {
        await request('/api/state', undefined, controller.signal);
        if (g === epoch && scope && navigator.onLine && !controller.signal.aborted) openSocket();
      } catch (e) {
        // Retry only after the in-flight preflight has been detached.
      } finally {
        clearTimeout(timeout);
        if (preflight === controller) preflight = null;
        if (g === epoch && scope && navigator.onLine && !socket) schedule();
      }
    }, delay);
  }
  function openSocket() {
    if (!scope || socket || !navigator.onLine) return;
    const g = epoch, {family, token} = scope;
    const ws = new WebSocket(`ws://${location.host}/__spike/socket?family=${encodeURIComponent(family)}&token=${encodeURIComponent(token)}`);
    socket = ws;
    const current = () => g === epoch && socket === ws;
    ws.onmessage = e => {
      if (!current()) return;
      let frame; try {frame = JSON.parse(e.data);} catch (_) {return;}
      if (frame?.type === 'invalidate' && Number.isSafeInteger(frame.revision) && frame.revision >= 0 && frame.revision > revision) reload();
    };
    ws.onopen = () => {if (current()) reload();};
    ws.onclose = ws.onerror = () => {
      if (!current()) return;
      socket = null; ws.onmessage = ws.onopen = ws.onclose = ws.onerror = null; ws.close();
      hooks?.clear(); status(navigator.onLine ? 'reconnecting' : 'offline'); schedule();
    };
  }
  async function connect(family, token) {
    disconnect(false);
    el('token').value = token;
    scope = {family, token};
    safetyTimer = setInterval(() => {if (scope && navigator.onLine && socket?.readyState === WebSocket.OPEN) reload();}, Math.max(250, safetyMs));
    openSocket();
  }
  window.__BOD_SYNTHETIC_SPIKE__ = {enabled:true, request, context:()=>epoch,
    accept(state, serial, settled) {
      if (!Number.isSafeInteger(state.revision) || state.revision < 0 || state.revision < revision || (state.revision === revision && (serial <= acceptedSerial || serial < settled))) return false;
      revision = state.revision; acceptedSerial = serial; return true;
    },
    attach(value) {hooks = value;}, connect, disconnect};
  el('connect').onclick = () => connect(el('family').value, el('token').value);
  el('logout').onclick = () => disconnect();
  el('family').onchange = () => disconnect();
  document.addEventListener('visibilitychange', () => {if (scope && document.visibilityState === 'visible' && navigator.onLine) {if (socket?.readyState === WebSocket.OPEN) reload(); else schedule();}});
  window.addEventListener('offline', () => {
    clearTimeout(retryTimer); retryTimer = null; preflight?.abort(); preflight = null;
    if (socket) {const old = socket; socket = null; old.onmessage = old.onopen = old.onclose = old.onerror = null; old.close();}
    if (scope) {hooks?.clear(); status('offline');}
  });
  window.addEventListener('online', () => {if (scope) schedule();});
  // Fixture event handlers may reject after their context has changed. Never let
  // a global rejection callback overwrite the current session's status.
  window.addEventListener('unhandledrejection', e => {e.preventDefault();});
})();