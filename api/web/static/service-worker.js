/**
 * Mutual service worker.
 *
 * Strategy:
 *  - Precache the app shell (CSS, manifest, icons) at install.
 *  - Cache-first for /static/* — they change with a deploy and are
 *    versioned via CACHE_VERSION below.
 *  - Network-first for HTML — we never want to serve a stale dashboard
 *    after a pool switch. If the network fails, fall back to a generic
 *    offline screen.
 *  - Never cache or intercept POST/PUT/DELETE. Audit-log integrity
 *    requires those to hit the server live.
 */
const CACHE_VERSION = 'mutual-v1';
const SHELL = [
  '/static/app.css',
  '/static/manifest.webmanifest',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/icon-maskable-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => cache.addAll(SHELL))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

const OFFLINE_HTML = `<!doctype html>
<html lang="en"><head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Offline — Mutual</title>
  <link rel="stylesheet" href="/static/app.css">
</head><body><main>
  <h1>You're offline.</h1>
  <p>Mutual needs the network to record contributions, claims, and votes
     so the audit log stays honest. Try again when you reconnect.</p>
</main></body></html>`;

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Cache-first for static assets and the manifest.
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        const copy = res.clone();
        caches.open(CACHE_VERSION).then((c) => c.put(req, copy));
        return res;
      }))
    );
    return;
  }

  // Network-first for HTML navigations.
  if (req.mode === 'navigate' || (req.headers.get('accept') || '').includes('text/html')) {
    event.respondWith(
      fetch(req).catch(() => new Response(OFFLINE_HTML, {
        headers: { 'Content-Type': 'text/html; charset=utf-8' },
        status: 503,
      }))
    );
    return;
  }
});
