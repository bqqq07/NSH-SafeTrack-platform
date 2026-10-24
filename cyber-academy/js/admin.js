// لوحة المشرف: متابعة تقدم المتدربين والتحكم بتدريبهم (إيقاف، سقف مستوى، فتح الدروس، تصفير، حسابات).
// الصلاحيات تُفحص على السيرفر في كل طلب؛ هذي الواجهة للعرض فقط.
import * as api from './api.js';
import { esc } from './glossary.js';
import * as sync from './sync.js';

/* ---------- أدوات صغيرة ---------- */

function toast(msg, bad = false) {
  const t = document.createElement('div');
  t.className = `toast ${bad ? 'bad' : ''}`;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2800);
}

// last_seen يجي من السيرفر بتوقيت UTC بصيغة "2026-10-23 08:15:00"
const parseUtc = (s) => (s ? new Date(`${s.replace(' ', 'T')}Z`) : null);
function ago(s) {
  const d = parseUtc(s);
  if (!d) return 'ما دخل بعد';
  const m = Math.floor((Date.now() - d.getTime()) / 60000);
  if (m < 2) return 'الحين';
  if (m < 60) return `قبل ${m} دقيقة`;
  if (m < 1440) return `قبل ${Math.floor(m / 60)} ساعة`;
  return `قبل ${Math.floor(m / 1440)} يوم`;
}
const daysSince = (s) => { const d = parseUtc(s); return d ? (Date.now() - d.getTime()) / 864e5 : Infinity; };

const levelsOf = (cur) => cur.levels;
const lv = (l) => (l.track === 'python' ? `P${l.id - 10}` : l.track === 'extras' ? `X${l.id - 20}` : `م${l.id}`);
const lessonTitle = (cur, id) => cur.levels.flatMap((l) => l.modules).find((m) => m.id === id)?.title_ar || id;
const pct = (done, total) => (total ? Math.round((done / total) * 100) : 0);
const displayName = (u) => esc(u.name || u.username);

const statusBadge = (u) => (u.status === 'suspended'
  ? '<span class="badge bad">⏸️ موقوف</span>'
  : '<span class="badge ok">● نشط</span>');

// نتأكد من أن العملية نجحت وإلا نعرض رسالة الخطأ
async function act(promise, okMsg) {
  const r = await promise;
  if (r.unavailable) { toast('تعذّر الاتصال بالسيرفر', true); return null; }
  if (r.error) { toast(r.error, true); return null; }
  if (okMsg) toast(okMsg);
  return r;
}

/* ---------- صفحة الإعداد الأولى للمشرف ---------- */

export function setupPage(view, onDone) {
  view.innerHTML = `
    <h1>🛠️ إنشاء حساب المشرف</h1>
    <p class="muted">تظهر هذي الصفحة مرة وحدة فقط، لين ينشأ أول مشرف. تحتاج مفتاح الإعداد (setup_key) الموجود في ملف <code>api/config.php</code> على السيرفر.</p>
    <form class="card" id="sf">
      <label>مفتاح الإعداد</label>
      <input class="search en" name="k" type="password" dir="ltr" autocomplete="off" required>
      <label>اسم المشرف <small class="muted">(إنجليزي، 3-30)</small></label>
      <input class="search en" name="u" dir="ltr" autocomplete="username" autocapitalize="off" required pattern="[A-Za-z0-9_]{3,30}">
      <label>كلمة المرور <small class="muted">(12 حرف أو أكثر)</small></label>
      <input class="search en" name="p" type="password" dir="ltr" autocomplete="new-password" required minlength="12">
      <div id="msg" class="feedback no" hidden></div>
      <button class="btn" type="submit">إنشاء المشرف</button>
    </form>`;
  view.querySelector('#sf').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const btn = e.target.querySelector('button[type=submit]'); btn.disabled = true;
    const r = await sync.setupAsAdmin(f.get('k'), f.get('u'), f.get('p'));
    if (r.user) return onDone();
    const m = view.querySelector('#msg'); m.hidden = false;
    m.textContent = r.unavailable ? 'السيرفر غير متاح' : r.error || 'صار خطأ';
    btn.disabled = false;
  });
}

/* ---------- الصفحة الرئيسية: قائمة المتدربين ---------- */

export async function adminHome(view, cur) {
  view.innerHTML = '<h1>🛠️ لوحة المشرف</h1><p class="muted">جاري التحميل...</p>';
  let ui = { filter: 'all', q: '', sort: 'recent' };
  let data;

  const load = async () => {
    const r = await act(api.adminList());
    if (!r) { view.innerHTML = '<div class="card"><p>تعذّر تحميل البيانات. تأكد أنك مسجّل دخول كمشرف.</p><a class="btn" href="#/">الرئيسية</a></div>'; return false; }
    data = r;
    return true;
  };

  const trainees = () => data.users.filter((u) => u.role === 'trainee');
  const visible = () => {
    let list = data.users.filter((u) => {
      if (ui.q && !`${u.username} ${u.name || ''}`.toLowerCase().includes(ui.q.toLowerCase())) return false;
      if (ui.filter === 'active') return u.status === 'active' && u.role === 'trainee';
      if (ui.filter === 'suspended') return u.status === 'suspended';
      if (ui.filter === 'new') return u.role === 'trainee' && u.done === 0;
      if (ui.filter === 'stale') return u.role === 'trainee' && u.status === 'active' && daysSince(u.last_seen) >= 7;
      if (ui.filter === 'admins') return u.role === 'admin';
      return true;
    });
    const key = { recent: (u) => -(parseUtc(u.last_seen)?.getTime() || 0), progress: (u) => -u.done, name: (u) => (u.name || u.username) }[ui.sort];
    return list.sort((a, b) => (key(a) > key(b) ? 1 : key(a) < key(b) ? -1 : 0));
  };

  const render = () => {
    const t = trainees();
    const activeToday = t.filter((u) => daysSince(u.last_seen) < 1).length;
    const suspended = t.filter((u) => u.status === 'suspended').length;
    const avg = t.length ? Math.round(t.reduce((n, u) => n + pct(u.done, data.total_lessons), 0) / t.length) : 0;
    const chip = (k, label) => `<button data-f="${k}" class="${ui.filter === k ? 'on' : ''}">${label}</button>`;
    view.innerHTML = `
      <h1>🛠️ لوحة المشرف</h1>
      <div class="grid3" style="grid-template-columns:repeat(2,1fr)">
        <div class="stat"><b>👥 ${t.length}</b><span>متدرب</span></div>
        <div class="stat"><b>🟢 ${activeToday}</b><span>نشط آخر 24 ساعة</span></div>
        <div class="stat"><b>⏸️ ${suspended}</b><span>موقوف</span></div>
        <div class="stat"><b>📈 ${avg}%</b><span>متوسط الإنجاز</span></div>
      </div>

      <div class="card">
        <div class="row" style="flex-wrap:wrap">
          <button class="btn red small" id="stopAll">⏸️ إيقاف التدريب للجميع</button>
          <button class="btn blue small" id="resumeAll">▶️ استئناف الجميع</button>
          <button class="btn ghost small" id="csv">⬇️ تصدير CSV</button>
        </div>
        <label class="row" style="margin-top:12px"><input type="checkbox" id="reg" ${data.registration_open ? 'checked' : ''}> السماح بالتسجيل الذاتي (إذا موقوف: أنت تنشئ الحسابات فقط)</label>
        <details style="margin-top:10px"><summary><b>➕ إنشاء حساب متدرب</b></summary>
          <form id="cf" style="margin-top:8px">
            <input class="search en" name="u" dir="ltr" placeholder="اسم المستخدم (إنجليزي)" required pattern="[A-Za-z0-9_]{3,30}" autocapitalize="off">
            <input class="search" name="n" placeholder="الاسم الكامل (اختياري)">
            <input class="search en" name="p" dir="ltr" placeholder="كلمة مرور مؤقتة (8+)" required minlength="8">
            <button class="btn small" type="submit">إنشاء</button>
          </form>
        </details>
      </div>

      <input class="search" id="q" type="search" placeholder="ابحث بالاسم..." value="${esc(ui.q)}">
      <div class="seg wrap">${chip('all', 'الكل')}${chip('active', 'نشط')}${chip('suspended', 'موقوف')}${chip('new', 'ما بدأ')}${chip('stale', 'متوقف +7 أيام')}${chip('admins', 'المشرفون')}</div>
      <div class="seg" style="margin:8px 0 14px"><span class="muted">ترتيب:</span>
        <button data-s="recent" class="${ui.sort === 'recent' ? 'on' : ''}">آخر دخول</button>
        <button data-s="progress" class="${ui.sort === 'progress' ? 'on' : ''}">التقدم</button>
        <button data-s="name" class="${ui.sort === 'name' ? 'on' : ''}">الاسم</button></div>
      <div id="list">${visible().map((u) => card(u)).join('') || '<p class="muted">ما فيه نتائج.</p>'}</div>`;
    bind();
  };

  const card = (u) => {
    const p = pct(u.done, data.total_lessons);
    return `<a class="card au" href="#/admin/u/${u.id}">
      <div class="row between"><b>${displayName(u)}${u.role === 'admin' ? ' 🛠️' : ''}</b>${statusBadge(u)}</div>
      <div class="muted en" style="font-size:.8rem">@${esc(u.username)}</div>
      <div class="bar" style="margin:8px 0"><i style="width:${p}%"></i></div>
      <div class="row between muted" style="font-size:.85rem"><span>${u.done}/${data.total_lessons} درس (${p}%)</span><span>⭐ ${u.xp} · 🔥 ${u.streak}</span></div>
      <div class="muted" style="font-size:.8rem">آخر دخول: ${ago(u.last_seen)}${u.in_progress ? ` · يدرس: ${esc(lessonTitle(cur, u.in_progress))}` : ''}</div>
      ${u.max_level !== null ? `<span class="badge">سقف: المستوى ${u.max_level}</span> ` : ''}${u.free_access ? '<span class="badge">🔓 كل الدروس مفتوحة</span>' : ''}
    </a>`;
  };

  const bind = () => {
    const refresh = async () => { if (await load()) render(); };
    view.querySelector('#q').addEventListener('input', (e) => { ui.q = e.target.value; view.querySelector('#list').innerHTML = visible().map(card).join('') || '<p class="muted">ما فيه نتائج.</p>'; });
    view.querySelectorAll('[data-f]').forEach((b) => b.addEventListener('click', () => { ui.filter = b.dataset.f; render(); }));
    view.querySelectorAll('[data-s]').forEach((b) => b.addEventListener('click', () => { ui.sort = b.dataset.s; render(); }));
    view.querySelector('#stopAll').addEventListener('click', async () => {
      if (!confirm('تبغى توقف التدريب لكل المتدربين؟ ما راح يقدرون يفتحون أو يحفظون أي تقدم لين تستأنف.')) return;
      const note = prompt('رسالة تظهر للمتدربين (اختياري):', 'التدريب موقوف مؤقتاً من المشرف') || '';
      if (await act(api.adminBulk('suspended', note), 'تم إيقاف التدريب للجميع')) refresh();
    });
    view.querySelector('#resumeAll').addEventListener('click', async () => {
      if (!confirm('استئناف التدريب لكل المتدربين؟')) return;
      if (await act(api.adminBulk('active'), 'تم استئناف التدريب')) refresh();
    });
    view.querySelector('#reg').addEventListener('change', async (e) => {
      const r = await act(api.adminSettings({ registration_open: e.target.checked }), e.target.checked ? 'التسجيل الذاتي مفتوح' : 'التسجيل الذاتي مغلق');
      if (!r) e.target.checked = !e.target.checked;
    });
    view.querySelector('#cf').addEventListener('submit', async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      if (await act(api.adminCreate(f.get('u'), f.get('p'), f.get('n')), 'تم إنشاء الحساب')) refresh();
    });
    view.querySelector('#csv').addEventListener('click', () => exportCsv(data, cur));
  };

  if (await load()) render();
}

function exportCsv(data, cur) {
  const head = ['username', 'name', 'role', 'status', 'lessons_done', 'lessons_total', 'percent', 'xp', 'avg_score', 'streak', 'last_seen_utc', 'max_level', 'in_progress'];
  const q = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const rows = data.users.map((u) => [u.username, u.name, u.role, u.status, u.done, data.total_lessons, pct(u.done, data.total_lessons), u.xp, u.avg_score, u.streak, u.last_seen, u.max_level, u.in_progress ? lessonTitle(cur, u.in_progress) : ''].map(q).join(','));
  // BOM عشان Excel يقرأ العربي صح
  const blob = new Blob(['﻿' + [head.join(','), ...rows].join('\n')], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `trainees-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/* ---------- صفحة متدرب واحد ---------- */

export async function adminUserPage(view, cur, id) {
  view.innerHTML = '<p class="muted">جاري التحميل...</p>';
  const me = sync.currentUser();

  const load = async () => {
    const r = await act(api.adminUser(id));
    if (!r) { view.innerHTML = '<div class="card"><p>تعذّر تحميل المتدرب.</p><a class="btn" href="#/admin">رجوع</a></div>'; return null; }
    return r;
  };

  const render = (d) => {
    const u = d.user, total = d.total_lessons, p = pct(u.done, total);
    const self = me && me.username === u.username;
    const lvOptions = (sel) => levelsOf(cur).map((l) => `<option value="${l.id}" ${sel === l.id ? 'selected' : ''}>${lv(l)}: ${esc(l.title_ar)}</option>`).join('');

    view.innerHTML = `
      <a href="#/admin" class="muted">← كل المتدربين</a>
      <h1>${displayName(u)} ${statusBadge(u)}</h1>
      <div class="muted en" style="margin-bottom:10px">@${esc(u.username)} · ${u.role === 'admin' ? 'مشرف' : 'متدرب'} · آخر دخول: ${ago(u.last_seen)}</div>
      ${u.status === 'suspended' ? `<div class="callout warn"><span class="label">⏸️ التدريب موقوف</span>${esc(u.note || 'بدون رسالة')}</div>` : ''}

      <div class="grid3" style="grid-template-columns:repeat(2,1fr)">
        <div class="stat"><b>${u.done}/${total}</b><span>دروس مكتملة (${p}%)</span></div>
        <div class="stat"><b>⭐ ${u.xp}</b><span>نقاط</span></div>
        <div class="stat"><b>${u.avg_score ?? '—'}${u.avg_score !== null ? '%' : ''}</b><span>متوسط الدرجات</span></div>
        <div class="stat"><b>🔥 ${u.streak}</b><span>أيام متتالية</span></div>
      </div>
      ${u.in_progress ? `<p class="muted">يدرس الآن: <b>${esc(lessonTitle(cur, u.in_progress))}</b> · مراجعة مصطلحات: ${d.review_count}</p>` : `<p class="muted">مراجعة مصطلحات: ${d.review_count}</p>`}

      <div class="card">
        <h3>التقدم حسب المستوى</h3>
        ${levelsOf(cur).map((l) => {
          const done = (u.levels && u.levels[l.id]) || 0, n = l.modules.filter((m) => m.ready).length;
          return `<div class="row between" style="font-size:.85rem"><span>${l.icon} ${lv(l)}: ${esc(l.title_ar)}</span><span>${done}/${n}</span></div><div class="bar" style="margin:3px 0 10px"><i style="width:${pct(done, n)}%"></i></div>`;
        }).join('')}
      </div>

      <div class="card">
        <h3>🎛️ التحكم بالتدريب</h3>
        ${self ? '<p class="muted">هذا حسابك أنت، ما تقدر توقفه.</p>' : u.status === 'active'
          ? `<label>رسالة للمتدرب عند الإيقاف (اختياري)</label><input class="search" id="note" placeholder="مثال: توقف مؤقت حتى تنتهي المراجعة">
             <button class="btn red" id="stop">⏸️ إيقاف التدريب</button>`
          : '<button class="btn blue" id="resume">▶️ استئناف التدريب</button>'}

        <label style="margin-top:14px">أقصى مستوى مسموح</label>
        <select class="search" id="cap"><option value="">بدون حد (كل المستويات)</option>${lvOptions(u.max_level)}</select>
        <label class="row" style="margin-top:8px"><input type="checkbox" id="free" ${u.free_access ? 'checked' : ''}> فتح كل الدروس ضمن السقف (إلغاء القفل المتسلسل)</label>

        <label style="margin-top:14px">الاسم</label>
        <div class="row"><input class="search" id="name" value="${esc(u.name || '')}" placeholder="الاسم الكامل" style="margin:0"><button class="btn small" id="saveName">حفظ</button></div>
      </div>

      <div class="card">
        <h3>🔁 تصفير التقدم</h3>
        <div class="row"><select class="search" id="rlevel" style="margin:0">${lvOptions(-1)}</select><button class="btn ghost small" id="resetLevel">صفّر المستوى</button></div>
        <button class="btn ghost" style="margin-top:10px" id="resetAll">صفّر كل التقدم</button>
      </div>

      <div class="card">
        <h3>🔑 الحساب</h3>
        <label>كلمة مرور جديدة (تعطيها للمتدرب)</label>
        <div class="row"><input class="search en" id="pw" dir="ltr" placeholder="${u.role === 'admin' ? '12+' : '8+'} أحرف" style="margin:0"><button class="btn small" id="setPw">تعيين</button></div>
        ${self ? '' : `<button class="btn ghost" style="margin-top:10px" id="role">${u.role === 'admin' ? 'إلغاء صلاحية المشرف' : 'ترقية لمشرف'}</button>
        <button class="btn ghost" style="margin-top:10px;color:var(--red)" id="del">🗑️ حذف الحساب نهائياً</button>`}
      </div>

      <details class="card"><summary><b>📚 الدروس بالتفصيل</b></summary>
        ${levelsOf(cur).map((l) => `<h3 style="margin-top:12px">${l.icon} ${lv(l)}: ${esc(l.title_ar)}</h3>` + l.modules.filter((m) => m.ready).map((m) => {
          const x = d.lessons[m.id];
          const s = x && x.done ? `✅ ${x.score}%` : x && x.slide ? '▶ قيد الدراسة' : '—';
          return `<div class="row between" style="font-size:.88rem;border-bottom:1px solid var(--line);padding:4px 0"><span>${esc(m.title_ar)}</span><span>${s}</span></div>`;
        }).join('')).join('')}
      </details>

      <details class="card"><summary><b>🧾 سجل إجراءات المشرفين</b></summary>
        ${d.audit.length ? d.audit.map((a) => `<div class="muted" style="font-size:.82rem;padding:3px 0">${esc(ago(a.at))} · ${esc(a.admin || '—')} · ${esc(a.action)} ${esc(a.detail || '')}</div>`).join('') : '<p class="muted">لا يوجد.</p>'}
      </details>`;

    const q = (s) => view.querySelector(s);
    const reload = async () => { const nd = await load(); if (nd) render(nd); };
    const upd = (fields, msg) => async () => { if (await act(api.adminUpdate(id, fields()), msg)) reload(); };

    q('#stop')?.addEventListener('click', async () => {
      if (!confirm(`إيقاف تدريب ${u.name || u.username}؟`)) return;
      if (await act(api.adminUpdate(id, { status: 'suspended', note: q('#note').value }), 'تم إيقاف التدريب')) reload();
    });
    q('#resume')?.addEventListener('click', upd(() => ({ status: 'active' }), 'تم استئناف التدريب'));
    q('#cap').addEventListener('change', upd(() => ({ max_level: q('#cap').value === '' ? null : +q('#cap').value }), 'تم تحديث السقف'));
    q('#free').addEventListener('change', upd(() => ({ free_access: q('#free').checked }), 'تم التحديث'));
    q('#saveName').addEventListener('click', upd(() => ({ full_name: q('#name').value }), 'تم حفظ الاسم'));
    q('#resetLevel').addEventListener('click', async () => {
      const lv = +q('#rlevel').value;
      if (!confirm(`تصفير كل دروس المستوى ${lv} لهذا المتدرب؟ ما يمكن التراجع.`)) return;
      if (await act(api.adminReset(id, lv), 'تم تصفير المستوى')) reload();
    });
    q('#resetAll').addEventListener('click', async () => {
      if (!confirm('تصفير كل تقدم هذا المتدرب (الدروس والمراجعة والنقاط)؟ ما يمكن التراجع.')) return;
      if (await act(api.adminReset(id, null), 'تم تصفير التقدم')) reload();
    });
    q('#setPw').addEventListener('click', async () => {
      if (await act(api.adminPassword(id, q('#pw').value), 'تم تعيين كلمة المرور')) q('#pw').value = '';
    });
    q('#role')?.addEventListener('click', async () => {
      if (!confirm(u.role === 'admin' ? 'إلغاء صلاحية المشرف؟' : 'ترقية هذا الحساب لمشرف؟ سيقدر يشوف ويتحكم بكل المتدربين.')) return;
      if (await act(api.adminUpdate(id, { role: u.role === 'admin' ? 'trainee' : 'admin' }), 'تم التحديث')) reload();
    });
    q('#del')?.addEventListener('click', async () => {
      if (!confirm(`حذف حساب ${u.username} وكل تقدمه نهائياً؟`)) return;
      if (await act(api.adminDelete(id), 'تم الحذف')) location.hash = '#/admin';
    });
  };

  const d = await load();
  if (d) render(d);
}
