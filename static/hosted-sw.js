/* Hosted mode must never serve another parent's cached page after logout. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.map(key => caches.delete(key))))
    .then(() => self.clients.claim()));
});
// Deliberately no fetch handler or offline fallback in hosted mode.
