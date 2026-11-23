// Service worker Тарілки (лише поза Telegram). Офлайн: оболонка застосунку, переглянуті
// сторінки API й фото. Нова версія — змінити VERSION; старі кеші видаляються при activate.
const VERSION = "v2";
const SHELL = `tarilka-shell-${VERSION}`; // index.html і хешовані assets
const API = "tarilka-api"; // GET /api/* — мережа першою, кеш як запасний варіант
const MEDIA = "tarilka-media"; // фото страв
const MEDIA_LIMIT = 400;
const API_TIMEOUT_MS = 5000;

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.add("/")).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k.startsWith("tarilka-shell-") && k !== SHELL).map((k) => caches.delete(k)),
      ))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/auth/")) return;
  if (req.mode === "navigate") return event.respondWith(navigation(req));
  if (url.pathname.startsWith("/assets/") || url.pathname.startsWith("/icons/")) {
    return event.respondWith(cacheFirst(SHELL, req));
  }
  if (url.pathname.startsWith("/media/")) return event.respondWith(media(req));
  if (url.pathname.startsWith("/api/")) return event.respondWith(networkFirst(req));
});

async function navigation(req) {
  try {
    const res = await fetch(req);
    if (res.ok) (await caches.open(SHELL)).put("/", res.clone());
    return res;
  } catch {
    return (await caches.match("/")) ?? Response.error();
  }
}

async function cacheFirst(name, req) {
  const hit = await caches.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) (await caches.open(name)).put(req, res.clone());
  return res;
}

async function media(req) {
  const cache = await caches.open(MEDIA);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) {
    await cache.put(req, res.clone());
    const keys = await cache.keys();
    await Promise.all(keys.slice(0, Math.max(0, keys.length - MEDIA_LIMIT)).map((k) => cache.delete(k)));
  }
  return res;
}

async function networkFirst(req) {
  const cache = await caches.open(API);
  try {
    const res = await Promise.race([
      fetch(req),
      new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), API_TIMEOUT_MS)),
    ]);
    if (res.ok) await cache.put(req, res.clone());
    else if (res.status === 401) await caches.delete(API); // сесію завершено — кеш чужий
    return res;
  } catch (err) {
    const hit = await cache.match(req);
    if (hit) return hit;
    throw err;
  }
}
