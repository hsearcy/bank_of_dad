/* Native modal dialogs provide Escape, focus containment and an inert backdrop. */
(() => {
  document.querySelectorAll('[data-info-dialog]').forEach(button => {
    const dialog = document.getElementById(button.dataset.infoDialog);
    button.addEventListener('click', () => {
      dialog.showModal();
      dialog.scrollTop = 0;
    });
    dialog.addEventListener('close', () => button.focus({preventScroll: true}));
    dialog.addEventListener('keydown', event => {
      if (event.key !== 'Tab') return;
      const controls = [...dialog.querySelectorAll('a[href], button:not([disabled])')];
      const first = controls[0], last = controls.at(-1);
      if (event.shiftKey && (document.activeElement === first || document.activeElement.matches('h2'))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
  });
})();
