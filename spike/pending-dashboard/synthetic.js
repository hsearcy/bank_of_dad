/* Unsafe fake session controls, exclusively served by the local spike. */
(() => {
  let hooks, scope = null, epoch = 0, socket = null, revision = -1, pendingSerial = 0;
  let retryTimer = null, safetyTimer = null, handshakeTimer = null, attempts = 0, acceptedSerial = 0, acceptedNow = -1, preflight = null;
  const handshakeMs = Number(window.__BOD_SYNTHETIC_HANDSHAKE_MS__) || 5000;
  // Test harness may accelerate this synthetic-only safety net.
  const safetyMs = Number(window.__BOD_SYNTHETIC_SAFETY_MS__) || 60000;
  const clearTimers = () => {clearTimeout(retryTimer); clearInterval(safetyTimer); clearTimeout(handshakeTimer); retryTimer = safetyTimer = handshakeTimer = null; preflight?.abort(); preflight = null;};
  const ui = document.createElement('aside');
  ui.style.cssText = 'position:fixed;bottom:0;right:0;z-index:200;background:white;max-width:440px;border:2px solid black;padding:4px';
  ui.innerHTML = `<b>READ-ONLY SYNTHETIC ONLY — fake grants, no real authentication</b>
    <select id="spike-family"><option value="${'a'.repeat(32)}">A</option><option value="${'b'.repeat(32)}">B</option></select>
    <input id="spike-token" placeholder="Fake scoped grant"><button id="spike-connect">Connect / reconnect</button>
    <button id="spike-logout">Logout</button><output id="spike-status">disconnected</output>`;
  document.body.prepend(ui);
  const panel = document.createElement('section');
  panel.id = 'spike-pending';
  panel.setAttribute('aria-live', 'polite');
  panel.style.cssText = 'background:white;color:black;padding:1rem;border:2px solid #555;white-space:pre-wrap;overflow-wrap:anywhere';
  ui.before(panel);
  const pendingText = text => {panel.textContent = 'SYNTHETIC PENDING — READ ONLY. No actual review or acknowledgment.\n' + text;};
  pendingText('Disconnected; pending status unknown.');
  const style = document.createElement('style');
  style.textContent = '#dadBtn,.btn-spend,.btn-save,.btn-bond,.reward-btn {display:none !important}';
  document.head.append(style);
  const el = id => document.getElementById('spike-' + id);
  const status = value => {el('status').textContent = value;};
  function disconnect(clear = true) {
    epoch++;
    pendingSerial++;
    pendingText('Disconnected; pending status unknown.');
    clearTimers(); attempts = 0; revision = -1; acceptedSerial = 0; acceptedNow = -1;
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
    try { result = await response.json(); } catch (_) { throw Error('Synthetic invalid response.'); }
    if (g !== epoch || scope !== s) throw stale();
    if (!response.ok || (result === null && path !== '/api/pending') || (result !== null && (typeof result !== 'object' || Array.isArray(result)))) throw Error('Synthetic request failed or unsupported.');
    return result;
  }
  async function refreshPending() {
    const g = epoch, s = scope, serial = ++pendingSerial;
    pendingText('Checking; pending status unknown.');
    try {
      const value = await request('/api/pending');
      if (g !== epoch || s !== scope || serial !== pendingSerial) return;
      if (value === null) {pendingText('No pending operation.'); return;}
      if (typeof value.operation_id !== 'string' || !value.request || typeof value.request !== 'object' || !Object.hasOwn(value, 'result') || value.acknowledged !== false) throw Error('Invalid pending response');
      pendingText('Operation ID: ' + value.operation_id + '\nAction and payload: ' + JSON.stringify(value.request, null, 2) + '\nResult: ' + JSON.stringify(value.result, null, 2));
    } catch (_) {
      if (g === epoch && s === scope && serial === pendingSerial) pendingText('Pending status unknown — unable to retrieve.');
    }
  }
  const reload = async () => {
    const g = epoch, ws = socket;
    // Pending transitions need not advance the domain revision.
    void refreshPending();
    try {
      const applied = (await hooks.refresh()) !== false;
      if (applied && g === epoch && ws === socket && ws?.readyState === WebSocket.OPEN) {
        attempts = 0;
        status('connected');
      }
      return applied;
    } catch (e) {
      if (e.name !== 'AbortError' && g === epoch && ws === socket) {
        if (!e.denied) status('snapshot failed');
      }
      return false;
    }
  };
  function schedule() {
    if (!scope || socket || retryTimer || preflight || !navigator.onLine) return;
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
      if (frame?.type === 'invalidate') reload();
    };
    ws.onopen = () => {if (current()) {clearTimeout(handshakeTimer); handshakeTimer = null; reload();}};
    const lost = () => {
      if (!current()) return;
      clearTimeout(handshakeTimer); handshakeTimer = null;
      socket = null; ws.onmessage = ws.onopen = ws.onclose = ws.onerror = null; ws.close();
      pendingSerial++; pendingText('Disconnected; pending status unknown.');
      hooks?.clear(); status(navigator.onLine ? 'reconnecting' : 'offline'); schedule();
    };
    ws.onclose = ws.onerror = lost;
    handshakeTimer = setTimeout(lost, handshakeMs);
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
      if (!Number.isSafeInteger(state.revision) || state.revision < 0 || typeof state.server_now !== 'number' || !Number.isFinite(state.server_now) || state.server_now < 0 || state.server_now > Number.MAX_SAFE_INTEGER || state.revision < revision) return false;
      if (state.revision === revision && (serial < settled || state.server_now < acceptedNow || (state.server_now === acceptedNow && serial <= acceptedSerial))) return false;
      revision = state.revision; acceptedNow = state.server_now; acceptedSerial = serial; return true;
    },
    attach(value) {hooks = value;}, connect, disconnect};
  el('connect').onclick = () => connect(el('family').value, el('token').value);
  el('logout').onclick = () => disconnect();
  el('family').onchange = () => disconnect();
  el('token').oninput = () => {if (scope) disconnect(false);};
  document.addEventListener('visibilitychange', () => {if (scope && document.visibilityState === 'visible' && navigator.onLine) {if (socket?.readyState === WebSocket.OPEN) reload(); else schedule();}});
  window.addEventListener('offline', () => {
    clearTimeout(retryTimer); clearTimeout(handshakeTimer); retryTimer = handshakeTimer = null; preflight?.abort(); preflight = null;
    if (socket) {const old = socket; socket = null; old.onmessage = old.onopen = old.onclose = old.onerror = null; old.close();}
    if (scope) {pendingSerial++; pendingText('Offline; pending status unknown.'); hooks?.clear(); status('offline');}
  });
  window.addEventListener('online', () => {if (scope) schedule();});
  // Fixture event handlers may reject after their context has changed. Never let
  // a global rejection callback overwrite the current session's status.
  window.addEventListener('unhandledrejection', e => {e.preventDefault();});
})();