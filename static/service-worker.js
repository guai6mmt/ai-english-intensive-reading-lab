const CACHE = "english-lab-reader-v3";
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", event => event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k.startsWith("english-lab-") && k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())));
// Reading data and authenticated responses always come from the server.
