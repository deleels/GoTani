/* GoTani service worker (D74). Version: __VERSION__
 * - Static assets (same-origin /static/ and pinned CDN libraries): cache-first.
 * - HTML pages and data (everything else): network-only; a failed page navigation shows /offline/.
 * - Never caches or replays POST requests: no offline writes.
 */
const VERSION = "__VERSION__";
const STATIC_CACHE = `gotani-static-${VERSION}`;
const OFFLINE_URL = "__OFFLINE__";
const PRECACHE = [__PRECACHE__, OFFLINE_URL];
const STATIC_PREFIX = "__STATIC_PREFIX__";
const CDN_HOSTS = ["cdn.jsdelivr.net", "cdnjs.cloudflare.com", "fonts.googleapis.com", "fonts.gstatic.com"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(STATIC_CACHE).then((cache) => cache.addAll(PRECACHE)));
  // Do not skipWaiting automatically: the page shows "Versi baru tersedia · Muat ulang".
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("gotani-static-") && k !== STATIC_CACHE).map((k) => caches.delete(k))))
      // Navigation preload: the page request starts while the worker boots, not after.
      .then(() => self.registration.navigationPreload && self.registration.navigationPreload.enable())
      .then(() => self.clients.claim())
  );
});

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") self.skipWaiting();
});

function isStatic(url) {
  if (url.origin === self.location.origin) return url.pathname.startsWith(STATIC_PREFIX);
  return CDN_HOSTS.includes(url.hostname);
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return; // writes always go straight to the network
  const url = new URL(request.url);

  if (isStatic(url)) {
    event.respondWith(
      caches.match(request).then((hit) => hit || fetch(request).then((response) => {
        if (response.ok || response.type === "opaque") {
          const copy = response.clone();
          caches.open(STATIC_CACHE).then((cache) => cache.put(request, copy));
        }
        return response;
      }))
    );
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith((async () => {
      try {
        return (await event.preloadResponse) || (await fetch(request));
      } catch (err) {
        return caches.match(OFFLINE_URL);
      }
    })());
  }
  // Other data requests: default network behaviour, nothing cached.
});
