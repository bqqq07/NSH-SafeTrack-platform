// Service Worker: يخلي المنصة تشتغل بدون نت.
// الاستراتيجية: نجرّب النت أولاً (عشان التحديثات توصل)، وإذا فشل نرجع للنسخة المحفوظة.
const CACHE = 'cyber-academy-v9';
const CORE = [
  './', 'index.html', 'manifest.webmanifest', 'icons/icon.svg',
  'css/style.css',
  'js/app.js', 'js/admin.js', 'js/store.js', 'js/glossary.js', 'js/player.js', 'js/api.js', 'js/sync.js',
  'js/codeui.js', 'js/pyerrors.js', 'js/pyrunner.js', 'js/pyworker.js', 'js/pyharness.py',
  'content/curriculum.json', 'content/glossary.json', 'content/labs.json', 'content/term_lessons.json'
];

// عند التثبيت نحفظ الملفات الأساسية وكل الدروس عشان تشتغل المنصة كاملة بدون نت.
// محرك Python (vendor/pyodide ≈ 13MB) ما نحمّله هنا: يتحمّل ويتخزّن أول مرة يشغّل المتعلم كوداً (طلبات نفس الموقع تنحفظ تلقائياً).
async function precache() {
  const c = await caches.open(CACHE);
  await c.addAll(CORE);
  try {
    const cur = await (await fetch('content/curriculum.json')).json();
    const lessons = cur.levels.flatMap((l) => l.modules.filter((m) => m.ready).map((m) => `content/lessons/${m.id}.json`));
    await Promise.allSettled(lessons.map((u) => c.add(u)));
  } catch { /* بدون نت وقت التثبيت: نكمل بالأساسي */ }
}

self.addEventListener('install', (e) => {
  e.waitUntil(precache().then(() => self.skipWaiting()));
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
  // طلبات الحسابات ما تنحفظ أبداً (بيانات خاصة ومتغيرة)
  if (req.method !== 'GET' || new URL(req.url).pathname.includes('/api/')) return;
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
