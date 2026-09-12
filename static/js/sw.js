/**
 * NSH SafeTrack — Service Worker
 * Caches static assets + visited pages so the site loads offline.
 * Strategy:
 *   /static/*  → cache-first (CSS/JS/images rarely change)
 *   HTML pages → network-first, fall back to cache
 *   /api/*     → network-only (never cache API calls)
 *   /uploads/* → network-only (large files, skip)
 */

var CACHE_NAME = 'nsh-v4';

// Pre-cached on install — the minimum needed to render any page
var PRECACHE = [
  '/static/css/style.css',
  '/static/js/offline.js',
  '/static/img/logo.png',
  '/offline',
];

// Pages to warm-cache on install (safety officer's daily pages).
// All of these are fetched in the background the first time the SW installs,
// so offline works immediately without the user visiting each page manually.
var WARM_PAGES = [
  // ── Daily field work ──
  '/location',
  '/hse/observation/new',
  '/hse/observations',
  '/env/',
  '/env/checklist/new',
  '/env/checklist/fill/P2-EC',
  '/env/checklist/fill/P2-WMC',
  '/env/checklist/fill/P2-SPC',
  '/welfare/observation/new',
  '/env/observation/new',
  '/hse/tbt/new',
  '/hse/tbt',
  '/hse/nearmiss/new',
  '/hse/nearmiss',
  '/hse/my-ca',

  // ── PTW Training home + all 7 modules ──
  '/ptw-training',
  '/ptw-training/module/1',
  '/ptw-training/module/2',
  '/ptw-training/module/3',
  '/ptw-training/module/4',
  '/ptw-training/module/5',
  '/ptw-training/module/6',
  '/ptw-training/module/7',

  // ── PTW Training: all 42 doors (7 modules × 6 doors each) ──
  '/ptw-training/module/1/door/1',
  '/ptw-training/module/1/door/2',
  '/ptw-training/module/1/door/3',
  '/ptw-training/module/1/door/4',
  '/ptw-training/module/1/door/5',
  '/ptw-training/module/1/door/6',
  '/ptw-training/module/2/door/1',
  '/ptw-training/module/2/door/2',
  '/ptw-training/module/2/door/3',
  '/ptw-training/module/2/door/4',
  '/ptw-training/module/2/door/5',
  '/ptw-training/module/2/door/6',
  '/ptw-training/module/3/door/1',
  '/ptw-training/module/3/door/2',
  '/ptw-training/module/3/door/3',
  '/ptw-training/module/3/door/4',
  '/ptw-training/module/3/door/5',
  '/ptw-training/module/3/door/6',
  '/ptw-training/module/4/door/1',
  '/ptw-training/module/4/door/2',
  '/ptw-training/module/4/door/3',
  '/ptw-training/module/4/door/4',
  '/ptw-training/module/4/door/5',
  '/ptw-training/module/4/door/6',
  '/ptw-training/module/5/door/1',
  '/ptw-training/module/5/door/2',
  '/ptw-training/module/5/door/3',
  '/ptw-training/module/5/door/4',
  '/ptw-training/module/5/door/5',
  '/ptw-training/module/5/door/6',
  '/ptw-training/module/6/door/1',
  '/ptw-training/module/6/door/2',
  '/ptw-training/module/6/door/3',
  '/ptw-training/module/6/door/4',
  '/ptw-training/module/6/door/5',
  '/ptw-training/module/6/door/6',
  '/ptw-training/module/7/door/1',
  '/ptw-training/module/7/door/2',
  '/ptw-training/module/7/door/3',
  '/ptw-training/module/7/door/4',
  '/ptw-training/module/7/door/5',
  '/ptw-training/module/7/door/6',
];

// ── Install ─────────────────────────────────────────────────────────────────
self.addEventListener('install', function (e) {
  e.waitUntil(
    caches.open(CACHE_NAME).then(function (cache) {
      // Pre-cache static assets silently — don't block install if a warm page fails
      return cache.addAll(PRECACHE).then(function () {
        return Promise.allSettled(
          WARM_PAGES.map(function (url) {
            return cache.add(url).catch(function () {/* ignore — user may not be logged in */});
          })
        );
      });
    }).then(function () { return self.skipWaiting(); })
  );
});

// ── Activate ─────────────────────────────────────────────────────────────────
// Delete old caches so stale files don't survive a version bump.
self.addEventListener('activate', function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(
        keys.filter(function (k) { return k !== CACHE_NAME; })
            .map(function (k) { return caches.delete(k); })
      );
    }).then(function () { return self.clients.claim(); })
  );
});

// ── Message: CACHE_ALL ───────────────────────────────────────────────────────
// Triggered by the "Download now" button after the user logs in.
// Fetches every warm page while authenticated and stores them in cache.

self.addEventListener('message', function (e) {
  if (!e.data || e.data.type !== 'CACHE_ALL') return;
  var client = e.source;

  caches.open(CACHE_NAME).then(function (cache) {
    var jobs = WARM_PAGES.map(function (url) {
      return fetch(url, { credentials: 'same-origin' })
        .then(function (res) {
          if (res.ok) return cache.put(url, res);
        })
        .catch(function () { /* ignore individual failures */ });
    });
    return Promise.allSettled(jobs);
  }).then(function () {
    // Also cache static assets
    return caches.open(CACHE_NAME).then(function (cache) {
      return cache.addAll(PRECACHE).catch(function(){});
    });
  }).then(function () {
    if (client) client.postMessage({ type: 'CACHE_ALL_DONE' });
  });
});

// ── Fetch ────────────────────────────────────────────────────────────────────
self.addEventListener('fetch', function (e) {
  var url = new URL(e.request.url);

  // Only handle same-origin requests
  if (url.origin !== self.location.origin) return;

  // Never intercept API calls or file uploads
  if (url.pathname.startsWith('/api/') ||
      url.pathname.startsWith('/uploads/') ||
      url.pathname.startsWith('/hse/photo/')) {
    return;
  }

  // Non-GET requests (POST, etc.) — don't cache
  if (e.request.method !== 'GET') return;

  // Static assets → cache-first
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(cacheFirst(e.request));
    return;
  }

  // HTML navigation → network-first with cache fallback
  e.respondWith(networkFirst(e.request));
});

// ── Strategies ───────────────────────────────────────────────────────────────

function cacheFirst(request) {
  return caches.match(request).then(function (cached) {
    if (cached) return cached;
    return fetch(request).then(function (response) {
      if (response && response.status === 200) {
        var clone = response.clone();
        caches.open(CACHE_NAME).then(function (c) { c.put(request, clone); });
      }
      return response;
    });
  });
}

function networkFirst(request) {
  return fetch(request).then(function (response) {
    // Cache successful HTML responses for offline use
    if (response && response.status === 200) {
      var clone = response.clone();
      caches.open(CACHE_NAME).then(function (c) { c.put(request, clone); });
    }
    return response;
  }).catch(function () {
    // Network failed → try cache
    return caches.match(request).then(function (cached) {
      if (cached) return cached;
      // Nothing cached for this page → show offline page
      return caches.match('/offline');
    });
  });
}
