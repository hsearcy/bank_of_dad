/* Optional, device-local helpers. Never write bank data or request permissions
   other than a user-selected Screen Wake Lock. */
const BodExperience = (() => {
  const find = selector => document.querySelector(selector);
  let tour, step = -1, target = null, describedBy = null, returnFocus = null;
  let steps = [];
  let paused = false;
  const allSteps = [
    ['#dadBtn', 'Your parent dashboard', 'Open the parent controls to add your kids, give money, set allowance and adjust rates. You can also save / sync a hosted bank there.'],
    [() => find('.reward-btn[data-kind="interest"]') || find('.btn-save'), 'Collect a little growth', 'Tap Savings Interest when it’s ready. It goes back into savings, so future interest can earn interest too.'],
    ['.btn-save', 'Give saving a try', 'Move money from the wallet into savings, or take savings out again. You choose the amount.'],
    ['.btn-bond', 'Practice patience', 'Invest money locks money for a chosen term. When it’s ready, collect the payout. Compare saving and investing on the growth chart.'],
    ['.wallet-actions', 'Additional tools', [
      ['💸', 'Spend', 'Record a purchase.'],
      ['🔮', 'What if', 'Preview growth without moving money.'],
      ['📜', 'History', 'See past activity.'],
      ['⛶', 'Expand graph', 'Open a bigger view.']
    ]],
    [null, 'A few things to know', 'I made this to track real balances for my own kids. It’s not a real bank.\n\nI like keeping it on an always-on tablet, but that’s optional.\n\nKids currently claim interest themselves, so they can feel their money grow.\n\nI set the default rates VERY HIGH so kids can see growth. You can change them in the parent dashboard.']
  ];
  const stepTarget = item => typeof item[0] === 'function' ? item[0]() : find(item[0]);
  function stepText(item, element) {
    if (item === allSteps[0] && !find('.btn-save')) {
      return `Open ${parentName()} to add your first kid. Then replay this tour to explore saving, interest and investing. Nothing is added or moved by the tour itself.`;
    }
    if (item === allSteps[5]) {
      const mode = STATE.config.auto_interest_enabled
        ? 'Automatic savings interest is on: it collects once every configured savings period.'
        : 'Manual interest collection is the default: kids claim interest themselves, so they can feel their money grow.';
      return item[2].replace('Kids currently claim interest themselves, so they can feel their money grow.', `${mode} ${parentName()} can change the optional automatic setting in the parent dashboard.`);
    }
    if (item === allSteps[1]) {
      if (STATE.config.auto_interest_enabled) return `Savings interest collects automatically once every configured savings period. Save money to grow savings; saving does not trigger an extra collection. ${parentName()} can switch back to manual collection in the parent dashboard.`;
      return element.matches('.reward-btn[data-kind="interest"]')
        ? item[2] + ' There’s a little ready to try now.'
        : 'Start with Save money to put money into savings. Savings Interest appears when there’s some ready to collect; it goes back into savings to earn interest too. Nothing moves during this tour.';
    }
    return item === allSteps[0] ? item[2].replace('Open Dad', `Open ${parentName()}`) : item[2];
  }
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
    if (tour.hidden || paused || step < 0) return;
    const item = steps[step];
    target = stepTarget(item);
    find('#tourText').textContent = target || !item[0]
      ? stepText(item, target)
      : 'This control is no longer available. Continue the tour, or open the parent dashboard to add a kid.';
    if (target && item === allSteps[4]) {
      const list = document.createElement('ul');
      list.className = 'tour-tools';
      for (const [icon, label, description] of item[2]) {
        const row = document.createElement('li');
        const bullet = document.createElement('span');
        bullet.textContent = icon;
        bullet.setAttribute('aria-hidden', 'true');
        const text = document.createElement('span');
        const name = document.createElement('b');
        name.textContent = label;
        text.append(name, ` — ${description}`);
        row.append(bullet, text);
        list.append(row);
      }
      find('#tourText').replaceChildren(list);
    }
    sizeTour();
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
    find('#tourTitle').textContent = `${step + 1} of ${steps.length} · ${steps[step] === allSteps[0] ? `${parentName()} is your dashboard` : steps[step][1]}`;
    find('#tourNext').textContent = step === steps.length - 1 ? 'Done' : 'Next';
    find('#tourTitle').focus({preventScroll: true});
    point(true);
  }
  // Real controls remain real: suspend only the guide while a panel is in
  // front. No clicks are cancelled, no form contents or bank state are touched.
  function syncPanels() {
    const blocked = !!find('.panel:not(.hidden), dialog[open]');
    if (tour.hidden || paused === blocked) return;
    paused = blocked;
    tour.classList.toggle('tour-paused', paused);
    tour.inert = paused;
    if (paused) {
      tour.setAttribute('aria-hidden', 'true');
      clearTarget();
    } else {
      tour.removeAttribute('aria-hidden');
      point(true);
      find('#tourTitle').focus({preventScroll: true});
    }
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
    // Only offer controls that exist in this bank; an empty bank starts with Dad.
    steps = allSteps.filter(item => !item[0] || stepTarget(item));
    step = -1;
    paused = false;
    tour.inert = false;
    tour.classList.remove('tour-paused');
    tour.removeAttribute('aria-hidden');
    tour.hidden = false;
    find('#tourTitle').textContent = 'A quick look around?';
    find('#tourText').textContent = 'Quick pointers, right here in your bank. Skip any time — or explore on your own.';
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
      status.textContent = '';
      lock.addEventListener('release', () => {
        if (sentinel !== lock) return;
        sentinel = null;
        // No retry loop when the OS/browser releases for battery or policy.
        status.textContent = wanted ? 'Not active — the browser released the wake lock. Toggle off and on to try again.' : '';
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
      if (event.key === 'Escape' && !tour.hidden && !paused) {
        event.preventDefault();
        closeTour();
      }
    });
    // Existing panels include dynamically inserted PIN gates and nested native
    // dialogs. Observe visibility only, not keystrokes or dashboard tick text.
    new MutationObserver(records => {
      if (records.some(record => record.type === 'attributes'
        ? record.target.matches('.panel, dialog')
        : record.target === document.body)) syncPanels();
    }).observe(document.body, {subtree: true, childList: true,
      attributes: true, attributeFilter: ['class', 'open']});
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
        status.textContent = '';
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
