// TimeStat service worker.
//
// Strategies:
//   * Static assets under /static/  -> stale-while-revalidate (serves cached,
//     refreshes in the background). Keeps the app usable offline / on flaky
//     networks and loads fast on repeat visits.
//   * Navigation (HTML document) requests -> network-first, falling back to a
//     cached copy of the dashboard shell so a reload while offline still works.
//   * Everything else (API, SSE, auth POSTs) -> passthrough; never cached.

const CACHE_VERSION = "timestat-cache-v2";
const CORE_ASSETS = [
  "/static/style.css",
  "/static/js/common.js",
  "/static/manifest.json",
  "/static/logo.svg",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE_VERSION);
      // core assets are best-effort; don't fail install if one is missing
      await cache.addAll(CORE_ASSETS).catch(() => {});
      await self.skipWaiting();
    })()
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k))
      );
      await self.clients.claim();
    })()
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  // Only handle same-origin requests; let cross-origin (CDNs) hit the network.
  if (url.origin !== self.location.origin) return;
  // Never intercept the SSE stream or any API call.
  if (url.pathname.startsWith("/api/")) return;

  // Navigation (HTML page loads): network-first with cached-shell fallback.
  if (request.mode === "navigate") {
    event.respondWith(
      (async () => {
        try {
          const fresh = await fetch(request);
          const cache = await caches.open(CACHE_VERSION);
          cache.put("/dashboard", fresh.clone()).catch(() => {});
          return fresh;
        } catch (_err) {
          const cached = await caches.match(request);
            return cached || (await caches.match("/dashboard")) || Response.error();
        }
      })()
    );
    return;
  }

  // Static assets: stale-while-revalidate.
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      (async () => {
        const cache = await caches.open(CACHE_VERSION);
        const cached = await cache.match(request);
        const network = fetch(request)
          .then((response) => {
            if (response && response.status === 200) {
              cache.put(request, response.clone()).catch(() => {});
            }
            return response;
          })
          .catch(() => null);
        return cached || (await network) || Response.error();
      })()
    );
  }
});
