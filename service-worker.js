const CACHE_NAME = 'divar-webapp-v2';
const APP_SHELL = [
  '/static/manifest.webmanifest',
  '/static/pwa.js',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png'
];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(APP_SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))
  )));
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/catalog') || url.pathname.startsWith('/ads') ||
      url.pathname.startsWith('/estimate/property') || url.pathname === '/health') return;

  if (request.mode === 'navigate') {
    // Authenticated pages contain session-specific navigation and CSRF tokens.
    // Keep them out of the shared app-shell cache.
    event.respondWith(fetch(request));
    return;
  }

  if (url.pathname.startsWith('/static/')) {
    event.respondWith(caches.match(request).then(cached => cached || fetch(request)));
  }
});
