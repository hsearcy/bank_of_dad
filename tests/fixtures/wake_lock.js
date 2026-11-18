/* Controlled API boundary only. The real template, controls and app JS run. */
(() => {
  const state = window.__wake = {calls: 0, releases: 0, mode: 'auto', locks: [], pending: [], visible: true};
  Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => state.visible ? 'visible' : 'hidden'});
  state.visibility = value => {
    state.visible = value;
    document.dispatchEvent(new Event('visibilitychange'));
  };
  state.resolve = () => state.pending.shift()();
  Object.defineProperty(navigator, 'wakeLock', {configurable: true, value: {
    request: async type => {
      if (type !== 'screen') throw new Error('Unexpected wake lock type');
      state.calls++;
      if (state.mode === 'deny') throw new DOMException('Synthetic denial', 'NotAllowedError');
      if (state.mode === 'hold') await new Promise(resolve => state.pending.push(resolve));
      const lock = new EventTarget();
      lock.released = false;
      lock.release = async () => {
        if (lock.released) return;
        state.releases++;
        lock.released = true;
        lock.dispatchEvent(new Event('release'));
      };
      state.locks.push(lock);
      return lock;
    }
  }});
})();
