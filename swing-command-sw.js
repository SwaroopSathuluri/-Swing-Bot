const CACHE_PREFIX = "swing-command-center-";
const CACHE_NAME = `${CACHE_PREFIX}v3`;
const APP_SHELL = [
  "swing-command-center.html",
  "fundamentals-data.json",
  "portfolio-market-data.json",
  "portfolio-tracker.css",
  "portfolio-tracker.js",
  "swing-command-manifest.webmanifest",
  "swing-command-icon.svg",
  "swing-command-icon-192.png",
  "swing-command-icon-512.png"
];
const CANONICAL_REFRESH_ASSETS = new Set([
  "fundamentals-data.json",
  "portfolio-market-data.json",
  "swingbot-pro-v2-data.json",
  "stocks-edge.html"
]);

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(APP_SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys
        .filter(key => key.startsWith(CACHE_PREFIX) && key !== CACHE_NAME)
        .map(key => caches.delete(key))
    ))
  );
  self.clients.claim();
});

self.addEventListener("fetch", event => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.origin !== location.origin) return;

  // Scheduled reports use cache-busting query strings. Keep one canonical offline
  // copy so repeated scheduled refreshes do not create unbounded cache entries.
  const filename = url.pathname.split("/").pop();
  if (CANONICAL_REFRESH_ASSETS.has(filename)) {
    const canonical = new Request(new URL(filename, self.registration.scope).href);
    event.respondWith(
      fetch(event.request)
        .then(response => {
          if (!response.ok) return response;
          const copy = response.clone();
          return caches.open(CACHE_NAME)
            .then(cache => cache.put(canonical, copy))
            .catch(() => undefined)
            .then(() => response);
        })
        .catch(() => caches.match(canonical).then(cached => cached || new Response(
          filename.endsWith(".json") ? JSON.stringify({ error: `${filename} is unavailable offline.` }) : `${filename} is unavailable offline.`,
          { status: 503, headers: { "Content-Type": filename.endsWith(".json") ? "application/json" : "text/plain" } }
        )))
    );
    return;
  }

  event.respondWith(
    fetch(event.request)
      .then(response => {
        const copy = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request).then(cached => cached || caches.match("swing-command-center.html")))
  );
});
