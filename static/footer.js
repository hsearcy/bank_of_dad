/* Native modal dialogs provide Escape and an inert backdrop. */
(() => {
  // Delegate so the tour can offer the same dialog without another footer link.
  const openers = new WeakMap();
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-info-dialog]');
    if (!button) return;
    const dialog = document.getElementById(button.dataset.infoDialog);
    if (!dialog || dialog.open) return;
    openers.set(dialog, button);
    dialog.showModal();
    const section = document.getElementById(button.dataset.infoSection);
    if (section && dialog.contains(section)) {
      section.focus({preventScroll: true});
      dialog.scrollTop += section.getBoundingClientRect().top - dialog.getBoundingClientRect().top - 24;
    } else {
      dialog.scrollTop = 0;
    }
  });
  document.querySelectorAll('dialog.info-dialog').forEach(dialog => {
    dialog.addEventListener('close', () => {
      const button = openers.get(dialog);
      if (button?.isConnected) button.focus({preventScroll: true});
      openers.delete(dialog);
    });
    dialog.addEventListener('keydown', event => {
      if (event.key !== 'Tab') return;
      const controls = [...dialog.querySelectorAll('a[href], button:not([disabled])')];
      const first = controls[0], last = controls.at(-1);
      if (event.shiftKey && (document.activeElement === first || document.activeElement.matches('h2, h3'))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
  });
})();
