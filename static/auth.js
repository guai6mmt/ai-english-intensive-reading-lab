(() => {
  const nativeFetch = window.fetch.bind(window);
  let csrfToken = "";
  let currentUser = null;

  const ready = nativeFetch("/api/auth/me", { credentials: "same-origin" })
    .then(async (response) => {
      if (!response.ok) {
        const next = encodeURIComponent(location.pathname + location.search);
        location.replace(`/login?next=${next}`);
        throw new Error("authentication required");
      }
      const data = await response.json();
      csrfToken = data.csrf_token || "";
      currentUser = data.user || null;
      return data;
    });

  window.fetch = async (input, init = {}) => {
    const requestUrl = new URL(typeof input === "string" ? input : input.url, location.href);
    const method = String(init.method || (typeof input !== "string" && input.method) || "GET").toUpperCase();
    const sameOrigin = requestUrl.origin === location.origin;
    const authEndpoint = requestUrl.pathname.startsWith("/api/auth/");
    const csrfExempt = ["/api/auth/status", "/api/auth/setup", "/api/auth/login"].includes(requestUrl.pathname);
    if (sameOrigin && !csrfExempt && !["GET", "HEAD", "OPTIONS"].includes(method)) {
      await ready;
      const headers = new Headers(init.headers || (typeof input !== "string" ? input.headers : undefined));
      headers.set("X-CSRF-Token", csrfToken);
      init = { ...init, headers, credentials: "same-origin" };
    } else if (sameOrigin) {
      init = { ...init, credentials: "same-origin" };
    }
    const response = await nativeFetch(input, init);
    if (sameOrigin && response.status === 401 && !authEndpoint) {
      const next = encodeURIComponent(location.pathname + location.search);
      location.replace(`/login?next=${next}`);
    }
    return response;
  };

  window.EnglishLabAuth = {
    ready,
    get user() { return currentUser; },
    get csrfToken() { return csrfToken; },
    async logout() {
      await ready;
      await window.fetch("/api/auth/logout", { method: "POST" });
      if ("caches" in window) {
        const keys = await caches.keys();
        await Promise.all(keys.filter((key) => key.startsWith("english-lab-")).map((key) => caches.delete(key)));
      }
      location.replace("/login");
    },
  };

  if ("serviceWorker" in navigator && location.protocol !== "file:") {
    window.addEventListener("load", () => navigator.serviceWorker.register("/service-worker.js").catch(() => {}));
  }
})();
