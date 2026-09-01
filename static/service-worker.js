const SHELL_CACHE = "english-lab-shell-v5";
const RUNTIME_CACHE = "english-lab-runtime-v1";
const OFFLINE_PACK_CACHE = "english-lab-article-packs-v1";
const SHELL = [
  "/static/index.html", "/static/styles.css", "/static/app.js",
  "/static/tokens.css", "/static/components.css", "/static/theme.js", "/static/auth.js",
  "/static/login.html", "/static/login.css", "/static/media.html", "/static/media.css",
  "/static/media.js", "/static/listen.html", "/static/listen.css", "/static/listen.js",
  "/static/icons/app-icon.svg", "/manifest.webmanifest",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  const keep = new Set([SHELL_CACHE, RUNTIME_CACHE, OFFLINE_PACK_CACHE]);
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((key) => key.startsWith("english-lab-") && !keep.has(key)).map((key) => caches.delete(key)))).then(() => self.clients.claim()));
});

async function networkFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const response = await fetch(request);
    // Cache API rejects partial (206) responses. Range audio requests should
    // stream normally; the explicit offline-pack action stores a full 200 copy.
    if (response.status === 200 && response.type !== "opaqueredirect") await cache.put(request, response.clone());
    return response;
  } catch (error) {
    const cached = await cache.match(request, { ignoreVary: true });
    if (cached) return cached;
    throw error;
  }
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin) return;

  const runtimeApi = ["/api/auth/me", "/api/library", "/api/vocabulary", "/api/progress"].includes(url.pathname);
  if (runtimeApi) {
    event.respondWith(networkFirst(request, RUNTIME_CACHE));
    return;
  }
  if (/^\/api\/articles\/[^/]+$/.test(url.pathname) || url.pathname.includes("/stream")) {
    event.respondWith(networkFirst(request, OFFLINE_PACK_CACHE));
    return;
  }
  if (url.pathname.startsWith("/api/")) return;
  event.respondWith(
    fetch(request).then((response) => {
      if (response.ok && response.type !== "opaqueredirect" && (url.pathname.startsWith("/static/") || url.pathname === "/manifest.webmanifest")) {
        caches.open(SHELL_CACHE).then((cache) => cache.put(request, response.clone()));
      }
      return response;
    }).catch(async () => {
      const cached = await caches.match(request, { ignoreVary: true });
      if (cached) return cached;
      if (request.mode === "navigate") return caches.match("/static/index.html");
      throw new Error("offline resource unavailable");
    })
  );
});

self.addEventListener("message", (event) => {
  const data = event.data || {};
  const port = event.ports?.[0];
  if (!port || !["CACHE_ARTICLE_PACK", "REMOVE_ARTICLE_PACK"].includes(data.type)) return;
  event.waitUntil((async () => {
    const cache = await caches.open(OFFLINE_PACK_CACHE);
    const urls = Array.isArray(data.urls) ? data.urls.filter((value) => typeof value === "string" && value.startsWith("/")) : [];
    if (data.type === "REMOVE_ARTICLE_PACK") {
      await Promise.all(urls.map((url) => cache.delete(url)));
      port.postMessage({ ok: true, removed: urls.length });
      return;
    }
    const stored = [];
    try {
      for (const url of urls) {
        const request = new Request(url, { credentials: "include" });
        const response = await fetch(request);
        if (!response.ok) throw new Error(`${url} (${response.status})`);
        await cache.put(request, response.clone());
        stored.push(url);
      }
      port.postMessage({ ok: true, stored: stored.length, articleId: data.articleId });
    } catch (error) {
      await Promise.all(stored.map((url) => cache.delete(url)));
      port.postMessage({ ok: false, error: `离线包保存失败：${error.message}` });
    }
  })());
});
