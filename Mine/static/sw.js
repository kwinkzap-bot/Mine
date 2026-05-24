// Minimal service worker — required by Chrome to show the install button.
// No caching: every request goes straight to the network as normal.
self.addEventListener('fetch', (event) => {
    event.respondWith(fetch(event.request));
});
