// Service Worker - PWA 离线缓存
const CACHE_NAME = "paper-tracker-v7";
const ASSETS = ["./", "./index.html", "./manifest.json", "./icon.svg", "./data/papers.json"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS).catch(() => {}))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  // 只处理 GET 请求
  if (req.method !== "GET") return;

  // papers.json 走网络优先（保证最新），失败回退缓存
  if (req.url.includes("papers.json")) {
    event.respondWith(
      fetch(req)
        .then((resp) => {
          const copy = resp.clone();
          caches.open(CACHE_NAME).then((c) => c.put(req, copy));
          return resp;
        })
        .catch(() => caches.match(req))
    );
    return;
  }

  // 其他资源缓存优先
  event.respondWith(caches.match(req).then((cached) => cached || fetch(req)));
});
