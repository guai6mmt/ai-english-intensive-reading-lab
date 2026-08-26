const CACHE_NAME = "english-lab-shell-v4";
const SHELL = [
  "/static/index.html",
  "/static/styles.css",
  "/static/app.js",
  "/static/tokens.css",
  "/static/components.css",
  "/static/theme.js",
  "/static/auth.js",
  "/static/login.html",
  "/static/login.css",
  "/static/media.html",
  "/static/media.css",
  "/static/media.js",
  "/static/listen.html",
  "/static/listen.css",
  "/static/listen.js",
  "/static/icons/app-icon.svg",
  "/manifest.webmanifest",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;
  if (url.pathname.includes("/stream")) return;
  event.respondWith(
    fetch(event.request).then((response) => {
      if (response.ok && response.type !== "opaqueredirect" && (url.pathname.startsWith("/static/") || url.pathname === "/manifest.webmanifest")) {
        const copy = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
      }
      return response;
    }).catch(() => caches.match(event.request).then((cached) => cached || caches.match("/static/index.html")))
  );
});
