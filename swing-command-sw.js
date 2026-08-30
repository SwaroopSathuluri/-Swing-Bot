const CACHE_PREFIX = "swing-command-center-";
const CACHE_NAME = `${CACHE_PREFIX}v2`;
const APP_SHELL = [
  "swing-command-center.html",
  "fundamentals-data.json",
  "swing-command-manifest.webmanifest",
  "swing-command-icon.svg",
  "swing-command-icon-192.png",
  "swing-command-icon-512.png"
];

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

  // Data requests use a cache-busting query string. Keep one canonical offline
  // copy so repeated scheduled refreshes do not create unbounded cache entries.
  if (url.pathname.endsWith("/fundamentals-data.json")) {
    const canonical = new Request(new URL("fundamentals-data.json", self.registration.scope).href);
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
          JSON.stringify({ error: "Fundamentals feed is unavailable offline." }),
          { status: 503, headers: { "Content-Type": "application/json" } }
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
