const CACHE_NAME = 'alqemma-shell-v1';

// Minimal app-shell cache so the service worker has a real fetch handler -
// required by several browsers (notably Edge) for install-eligibility.
// This does NOT try to make the app work offline (it's a live POS system
// backed by SQLite - offline pages would show stale/wrong data), it just
// falls straight through to the network for everything.
self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  // Always go to the network. This handler's only job is to exist, so the
  // browser recognizes an active service worker controlling the page.
  event.respondWith(fetch(event.request));
});

self.addEventListener('push', (event) => {
  let payload = { title: 'Al-Qemma Alert', body: 'You have a new notification.', url: '/' };

  if (event.data) {
    try {
      payload = event.data.json();
    } catch (e) {
      payload.body = event.data.text();
    }
  }

  const options = {
    body: payload.body,
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png',
    data: {
      url: payload.url || '/',
      tag: payload.tag || 'alqemma-notification',
    },
  };

  event.waitUntil(self.registration.showNotification(payload.title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const rawUrl = event.notification.data?.url || '/';
  const targetUrl = new URL(rawUrl, self.location.origin).toString();

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        const clientUrl = new URL(client.url);
        if (clientUrl.origin === self.location.origin && clientUrl.pathname === new URL(targetUrl).pathname && 'focus' in client) {
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(targetUrl);
      }
    })
  );
});
