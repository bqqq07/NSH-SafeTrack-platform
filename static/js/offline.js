/**
 * NSH SafeTrack — Offline Queue
 * Saves form submissions to localStorage when offline,
 * auto-syncs to /api/sync when the page loads and internet is back.
 */
(function () {
  'use strict';

  var QUEUE_KEY = 'nsh_offline_queue';
  var BANNER_ID = 'nsh-offline-banner';

  // ── Queue helpers ──────────────────────────────────────────────────────

  function getQueue() {
    try { return JSON.parse(localStorage.getItem(QUEUE_KEY) || '[]'); }
    catch (e) { return []; }
  }

  function saveQueue(q) {
    try { localStorage.setItem(QUEUE_KEY, JSON.stringify(q)); }
    catch (e) { console.warn('NSH offline: localStorage write failed'); }
  }

  function enqueue(type, data) {
    var q = getQueue();
    q.push({
      id:   (typeof crypto !== 'undefined' && crypto.randomUUID)
              ? crypto.randomUUID()
              : Date.now() + '-' + Math.random().toString(36).slice(2),
      type: type,
      data: data,
      ts:   Date.now()
    });
    saveQueue(q);
    renderBanner();
  }

  // ── Banner ─────────────────────────────────────────────────────────────

  function renderBanner() {
    var q = getQueue();
    var banner = document.getElementById(BANNER_ID);

    if (!q.length) {
      if (banner) banner.hidden = true;
      return;
    }

    if (!banner) {
      banner = document.createElement('div');
      banner.id = BANNER_ID;
      banner.style.cssText = [
        'position:fixed', 'bottom:0', 'left:0', 'right:0', 'z-index:9999',
        'background:#92400e', 'color:#fff',
        'display:flex', 'align-items:center', 'gap:12px',
        'padding:11px 16px', 'font-size:14px', 'font-family:inherit',
        'box-shadow:0 -2px 10px rgba(0,0,0,.3)'
      ].join(';');
      banner.innerHTML =
        '<span id="' + BANNER_ID + '-txt" style="flex:1"></span>' +
        '<button id="' + BANNER_ID + '-btn" ' +
          'style="background:rgba(255,255,255,.2);border:1px solid rgba(255,255,255,.4);' +
          'color:#fff;padding:5px 14px;border-radius:6px;font-size:13px;' +
          'font-weight:600;cursor:pointer;font-family:inherit">' +
          'Sync Now' +
        '</button>';
      document.body.appendChild(banner);
      document.getElementById(BANNER_ID + '-btn').addEventListener('click', syncQueue);
    }

    banner.hidden = false;
    var label = q.length === 1 ? '1 record saved offline' : q.length + ' records saved offline';
    document.getElementById(BANNER_ID + '-txt').textContent =
      label + ' — will upload when connected';
  }

  // ── Form intercept ──────────────────────────────────────────────────────
  // Runs in capture phase so it fires before the form's own submit handlers.

  document.addEventListener('submit', function (e) {
    var form = e.target;
    var offlineType = form.dataset && form.dataset.offlineType;
    if (!offlineType) return;   // form has no offline support → normal submit
    if (navigator.onLine) return; // online → Flask handles it with photos too

    e.preventDefault();
    e.stopImmediatePropagation();

    // Extract text/select/textarea values — skip file inputs
    var data = {};
    var els = form.elements;
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (!el.name) continue;
      if (el.type === 'file' || el.type === 'submit' || el.type === 'button') continue;
      if (el.type === 'checkbox') {
        // For single checkboxes store true/false; for groups with same name collect checked values as array
        if (el.checked) {
          var _existing = data[el.name];
          if (_existing === undefined || _existing === false) {
            data[el.name] = el.value;
          } else if (Array.isArray(_existing)) {
            _existing.push(el.value);
          } else {
            data[el.name] = [_existing, el.value];
          }
        } else if (!(el.name in data)) {
          data[el.name] = false;
        }
        continue;
      }
      if (el.type === 'radio') {
        if (el.checked) data[el.name] = el.value;
        continue;
      }
      // Multi-value array fields (e.g. emp_number[], emp_name[])
      if (el.name.slice(-2) === '[]') {
        if (!data[el.name]) data[el.name] = [];
        data[el.name].push(el.value);
      } else {
        data[el.name] = el.value;
      }
    }

    enqueue(offlineType, data);
    showFlash(
      '✓ Saved offline (no photos) — will upload when connected. ' +
      'You can add photos from the list after sync.',
      'success'
    );
  }, true);

  // ── Flash helper ───────────────────────────────────────────────────────

  function showFlash(msg, type) {
    var container = document.querySelector('.flash') ||
                    document.querySelector('main.container') ||
                    document.querySelector('main') ||
                    document.body;
    var div = document.createElement('div');
    div.className = 'alert ' + (type || 'info');
    div.style.cssText = 'margin:0 0 10px';
    div.textContent = msg;
    container.insertBefore(div, container.firstChild);
    setTimeout(function () { if (div.parentNode) div.parentNode.removeChild(div); }, 6000);
  }

  // ── Sync ────────────────────────────────────────────────────────────────

  function syncQueue() {
    var q = getQueue();
    if (!q.length || !navigator.onLine) return;

    var btn = document.getElementById(BANNER_ID + '-btn');
    if (btn) btn.textContent = 'Syncing…';

    var csrf = '';
    var csrfMeta = document.querySelector('meta[name="csrf-token"]');
    if (csrfMeta) csrf = csrfMeta.content;

    fetch('/api/sync', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrf
      },
      body: JSON.stringify(q)
    })
    .then(function (res) { return res.json().then(function(body){ return {ok: res.ok, body: body}; }); })
    .then(function (result) {
      if (result.ok) {
        var failed = new Set(result.body.failed || []);
        // Keep only items that failed; remove successfully synced ones
        var remaining = q.filter(function (item) { return failed.has(item.id); });
        saveQueue(remaining);
        renderBanner();
        var synced = q.length - remaining.length;
        if (!remaining.length) {
          showFlash('✓ ' + synced + ' record' + (synced > 1 ? 's' : '') + ' synced successfully. Add photos from the list.', 'success');
        } else {
          showFlash((remaining.length) + ' record(s) failed to sync — will retry next time.', 'warning');
        }
      } else {
        if (btn) btn.textContent = 'Sync Now';
      }
    })
    .catch(function () {
      if (btn) btn.textContent = 'Sync Now';
    });
  }

  // ── Init ────────────────────────────────────────────────────────────────

  function init() {
    renderBanner();
    if (navigator.onLine && getQueue().length) syncQueue();
    window.addEventListener('online', syncQueue);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // Public API — used by templates with custom JS (e.g. user_location.html)
  window.nshOffline = { enqueue: enqueue, showFlash: showFlash };

})();
