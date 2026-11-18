/* Optional, device-local helpers. Never write bank data or request permissions
   other than a user-selected Screen Wake Lock. */
const BodExperience = (() => {
  const find = selector => document.querySelector(selector);
  let tour, step = -1, target = null, describedBy = null, returnFocus = null;
  const steps = [
    ['#dadBtn', 'Dad is your dashboard', 'Open Dad to add your kids, give money, set allowance and adjust rates. You can also save / sync a hosted bank there.'],
    ['.reward-btn[data-kind="interest"], .btn-save', 'Collect a little growth', 'Tap Savings Interest when it’s ready. It goes back into savings, so future interest can earn interest too.'],
    ['.btn-save', 'Give saving a try', 'Save coins moves money from the wallet into savings. You choose the amount, and can take savings out again. Nothing moves during this tour.'],
    ['.btn-bond', 'Practice patience', 'Invest coins locks money for a chosen term. When it’s ready, collect the payout. Compare saving and investing on the growth chart.']
  ];
  function clearTarget() {
    if (!target) return;
    target.classList.remove('tour-target');
    if (describedBy === null) target.removeAttribute('aria-describedby');
    else target.setAttribute('aria-describedby', describedBy);
    target = null;
  }
  function sizeTour() {
    document.body.style.setProperty('--tour-space', `${tour.hidden ? 0 : tour.offsetHeight + 28}px`);
  }
  function point(scroll = false) {
    clearTarget();
    if (tour.hidden || step < 0) return;
    target = find(steps[step][0]);
    if (!target) return;
    describedBy = target.getAttribute('aria-describedby');
    target.classList.add('tour-target');
    target.setAttribute('aria-describedby', [describedBy, 'tourText'].filter(Boolean).join(' '));
    if (scroll) {
      // Leave both the target and the nonmodal guide visible, even on a phone.
      // Instant scrolling intentionally also respects reduced-motion preferences.
      const rect = target.getBoundingClientRect();
      const available = tour.getBoundingClientRect().top - 16;
      const top = Math.max(16, (available - rect.height) / 2);
      window.scrollBy({top: rect.top - top, behavior: 'instant'});
    }
  }
  function showStep() {
    find('#tourTitle').textContent = `${step + 1} of ${steps.length} · ${steps[step][1]}`;
    find('#tourText').textContent = steps[step][2] + (step === 1 && find('.reward-btn[data-kind="interest"]') ? ' There’s a little ready to try now.' : '');
    find('#tourNext').textContent = step === steps.length - 1 ? 'Done' : 'Next';
    sizeTour();
    find('#tourTitle').focus({preventScroll: true});
    point(true);
  }
  function closeTour(restoreFocus = true) {
    if (tour.hidden) return;
    clearTarget();
    tour.hidden = true;
    sizeTour();
    step = -1;
    if (restoreFocus) {
      if (returnFocus?.isConnected && returnFocus.getClientRects().length) returnFocus.focus({preventScroll: true});
      else find('#dadBtn').focus({preventScroll: true});
    }
  }
  function offer() {
    returnFocus = document.activeElement.closest('button, a, input, [tabindex]') || find('#dadBtn');
    step = -1;
    tour.hidden = false;
    find('#tourTitle').textContent = 'A quick look around?';
    find('#tourText').textContent = 'Four quick pointers, right here in your bank. Skip any time — or explore on your own.';
    find('#tourNext').textContent = 'Show me around';
    sizeTour();
    find('#tourNext').focus({preventScroll: true});
  }

  // A per-page opt-in, deliberately off again on reload. The switch represents
  // intent; the live status reports whether the browser actually holds a lock.
  let wanted = false, sentinel = null, generation = 0, pending = null, away = false;
  let toggle, status;
  const visible = () => !away && document.visibilityState === 'visible';
  const supported = () => window.isSecureContext && !!navigator.wakeLock?.request;
  function release(lock) { if (lock && !lock.released) Promise.resolve(lock.release()).catch(() => {}); }
  function pauseWake() {
    generation++;
    const old = sentinel;
    sentinel = null;
    release(old);
    if (wanted) status.textContent = 'Paused while this page is hidden. Will try again when you return.';
  }
  async function requestWake() {
    if (!wanted || !visible() || sentinel || pending || !supported()) return;
    const requestGeneration = generation;
    const request = {};
    pending = request;
    status.textContent = 'Requesting screen wake lock…';
    try {
      const lock = await navigator.wakeLock.request('screen');
      if (generation !== requestGeneration || !wanted || !visible()) {
        release(lock);
        return;
      }
      if (lock.released) {
        status.textContent = 'Not active — the browser released the wake lock. Toggle off and on to try again.';
        return;
      }
      sentinel = lock;
      status.textContent = 'Active — keeping this visible screen awake.';
      lock.addEventListener('release', () => {
        if (sentinel !== lock) return;
        sentinel = null;
        // No retry loop when the OS/browser releases for battery or policy.
        status.textContent = wanted ? 'Not active — the browser released the wake lock. Toggle off and on to try again.' : 'Off';
      });
    } catch (error) {
      if (generation === requestGeneration && wanted && visible()) {
        status.textContent = 'Not active — your browser or device declined the wake lock. Toggle off and on to try again.';
      }
    } finally {
      if (pending === request) pending = null;
      // An off/on or hide/show during an old request deserves exactly one new
      // attempt, but ordinary denial/release never retries itself.
      if (generation !== requestGeneration && wanted && visible()) requestWake();
    }
  }
  function init() {
    tour = find('#productTour');
    find('#tourSkip').onclick = () => closeTour();
    find('#tourNext').onclick = () => {
      if (++step >= steps.length) closeTour();
      else showStep();
    };
    find('#replayTour').onclick = () => {
      // Use the existing close path so the Dad PIN is forgotten, as usual.
      closeDad();
      offer();
    };
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !tour.hidden) {
        event.preventDefault();
        closeTour();
      }
    });
    // Leave the guide behind when opening real controls; it must not cover them.
    document.addEventListener('click', event => {
      if (!tour.hidden && event.target.closest('#dadBtn, #kids button')) closeTour(false);
    });
    window.addEventListener('resize', () => { sizeTour(); point(true); });

    toggle = find('#keepAwake');
    status = find('#awakeStatus');
    if (!supported()) {
      toggle.disabled = true;
      status.textContent = window.isSecureContext ? 'Unavailable — this browser does not support screen wake lock.' : 'Unavailable — screen wake lock needs HTTPS (or localhost).';
    }
    toggle.onchange = () => {
      wanted = toggle.checked;
      generation++;
      if (wanted) {
        if (visible()) requestWake();
        else status.textContent = 'Paused while this page is hidden. Will try again when you return.';
      } else {
        const old = sentinel;
        sentinel = null;
        release(old);
        status.textContent = 'Off';
      }
    };
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState !== 'visible') pauseWake();
      else requestWake();
    });
    window.addEventListener('pagehide', () => { away = true; pauseWake(); });
    window.addEventListener('pageshow', () => { away = false; requestWake(); });
  }
  return {init, offer, rendered: () => { if (tour) point(); }};
})();
