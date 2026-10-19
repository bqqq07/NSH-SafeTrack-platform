// Service Worker: يخلي المنصة تشتغل بدون نت.
// الاستراتيجية: نجرّب النت أولاً (عشان التحديثات توصل)، وإذا فشل نرجع للنسخة المحفوظة.
const CACHE = 'cyber-academy-v1';
const CORE = [
  './', 'index.html', 'manifest.webmanifest', 'icons/icon.svg',
  'css/style.css',
  'js/app.js', 'js/store.js', 'js/glossary.js', 'js/player.js',
  'content/curriculum.json', 'content/glossary.json'
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(CORE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  e.respondWith(
    fetch(req)
      .then((res) => {
        // نحفظ نسخة من كل شي من نفس الموقع (دروس، صور...) للاستخدام بدون نت
        if (res.ok && new URL(req.url).origin === location.origin) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return res;
      })
      .catch(() => caches.match(req).then((hit) => hit || caches.match('index.html')))
  );
});
