// نقطة البداية: الراوتر البسيط (hash) + الشاشات الرئيسية.
import * as store from './store.js';
import { loadGlossary, initTermEvents, allTerms, getTerm, speak, esc, closeSheet } from './glossary.js';
import { playLesson } from './player.js';
import * as sync from './sync.js';
import * as api from './api.js';
import { adminHome, adminUserPage, setupPage } from './admin.js';

const view = document.getElementById('view');
const nav = document.getElementById('nav');
let curriculum;

const lessonsOf = () => curriculum.levels.flatMap((l) => l.modules.map((m) => ({ ...m, level: l })));
const isDone = (id) => !!store.get().lessons[id]?.done;
// القفل المتسلسل: الدرس يفتح بعد إنهاء اللي قبله (بترتيب المنهج عبر المستويات).
// الدرس اللي انتهى سابقاً يبقى مفتوحاً للمراجعة.
// كل مسار (الأمن / Python) له قفله المتسلسل الخاص، فمتعلم الأمن ما يحتاج يخلص مساره عشان يبدأ بايثون
const trackOf = (id) => lessonsOf().find((m) => m.id === id)?.level.track || 'security';
const readyList = (track) => lessonsOf().filter((m) => m.ready && (!track || m.level.track === track));
const prevOf = (id) => { const l = readyList(trackOf(id)); const i = l.findIndex((m) => m.id === id); return i > 0 ? l[i - 1] : null; };
const levelLabel = (l) => (l.track === 'python' ? `P${l.id - 10}` : l.track === 'extras' ? `ملحق ${l.id - 20}` : `المستوى ${l.id}`);
const TRACKS = { security: ['🛡️', 'الأمن السيبراني'], python: ['🐍', 'Python'], extras: ['🧰', 'ملحقات'] };
let pathTrack = 'security';
// تحكم المشرف: إيقاف التدريب، وسقف للمستوى، وفتح كل الدروس
const me = () => sync.currentUser();
const isSuspended = () => me()?.status === 'suspended';
const levelOf = (id) => +id.match(/^m(\d+)\./)[1];
const overCap = (id) => { const c = me()?.max_level; return c !== null && c !== undefined && levelOf(id) > c; };
const isUnlocked = (id) => {
  if (isSuspended() || overCap(id)) return false;
  if (me()?.free_access) return true;
  const p = prevOf(id);
  return !p || isDone(id) || isDone(p.id);
};
// الدرس التالي = أول درس جاهز ما انحل وضمن السقف المسموح
const nextLesson = (track = 'security') => lessonsOf().find((m) => m.ready && m.level.track === track && !isDone(m.id) && !overCap(m.id));

// شاشة الإيقاف: يوقفها المشرف ويكتب فيها رسالة للمتدرب
function suspendedScreen() {
  nav.classList.remove('hidden');
  view.innerHTML = `<div class="card result"><div class="big">⏸️</div><h2>تدريبك موقوف حالياً</h2>
    <p>${esc(me()?.note || 'أوقف المشرف تدريبك مؤقتاً.')}</p>
    <p class="muted">تواصل مع مشرفك لاستئناف التدريب.</p>
    <button class="btn" id="chk">🔄 تحقق من الحالة</button><br>
    <a class="btn ghost" href="#/profile">حسابي</a></div>`;
  // نسأل السيرفر فوراً: ممكن المشرف استأنف التدريب
  const recheck = async () => { if (await sync.refresh()) route(); };
  view.querySelector('#chk').addEventListener('click', recheck);
  recheck();
}

/* ---------------- الشاشات ---------------- */

function home() {
  const st = store.get();
  const next = nextLesson('security');
  const pyNext = nextLesson('python');
  const ready = readyList('security');
  const done = ready.filter((m) => isDone(m.id)).length;
  const pyReady = readyList('python');
  const pyDone = pyReady.filter((m) => isDone(m.id)).length;
  const exNext = nextLesson('extras');
  const exReady = readyList('extras');
  const exDone = exReady.filter((m) => isDone(m.id)).length;
  const terms = allTerms();
  // "مصطلح اليوم" يتغير كل يوم ويثبت طول اليوم
  const wod = terms[Math.floor(Date.now() / 864e5) % terms.length];

  view.innerHTML = `
    <h1>هلا فيك 👋</h1>
    <p class="muted">تعلّم الأمن السيبراني من الصفر، خطوة خطوة.</p>
    <div class="grid3">
      <div class="stat"><b>🔥 ${st.streak}</b><span>أيام متتالية</span></div>
      <div class="stat"><b>⭐ ${st.xp}</b><span>نقاط XP</span></div>
      <div class="stat"><b>🏅</b><span>${store.rank().name}</span></div>
    </div>
    ${next ? `
      <a class="card" style="display:block;text-decoration:none;color:inherit;border-color:var(--green)" href="#/lesson/${next.id}">
        <span class="badge">${st.lessons[next.id] ? 'كمّل من وين وقفت' : 'الدرس التالي'}</span>
        <h2>${esc(next.title_ar)}</h2>
        <p class="muted en">${esc(next.title_en)}</p>
        <div class="btn">${st.lessons[next.id] ? 'كمّل' : 'ابدأ'} ←</div>
      </a>` : me()?.max_level != null && ready.some((m) => !isDone(m.id))
        ? `<div class="card"><h2>🏁 وصلت للحد المسموح</h2><p>أنهيت كل دروس المستويات المفتوحة لك. تواصل مع مشرفك لفتح المستوى التالي.</p></div>`
        : `<div class="card"><h2>🎉 خلصت كل الدروس المتاحة</h2><p>دروس جديدة جاية قريب.</p></div>`}
    <div class="card soft">
      <div class="row between"><b>🛡️ مسار الأمن</b><span class="muted">${done}/${ready.length} دروس</span></div>
      <div class="bar" style="margin-top:8px"><i style="width:${ready.length ? (done / ready.length) * 100 : 0}%"></i></div>
    </div>
    ${pyReady.length ? `<a class="card" style="display:block;text-decoration:none;color:inherit;border-color:var(--blue)" href="${pyNext ? `#/lesson/${pyNext.id}` : '#/path'}">
      <div class="row between"><b>🐍 مسار Python</b><span class="muted">${pyDone}/${pyReady.length} دروس</span></div>
      <div class="bar" style="margin:8px 0"><i style="width:${(pyDone / pyReady.length) * 100}%;background:var(--blue)"></i></div>
      <small class="muted">${pyNext ? `${pyDone ? 'كمّل' : 'ابدأ'}: ${esc(pyNext.title_ar)}` : 'خلصت الدروس المتاحة'}</small></a>` : ''}
    ${exReady.length ? `<a class="card" style="display:block;text-decoration:none;color:inherit;border-color:var(--orange, var(--blue))" href="${exNext ? `#/lesson/${exNext.id}` : '#/path'}">
      <div class="row between"><b>🧰 ملحقات (Git وBash وFlask وpandas)</b><span class="muted">${exDone}/${exReady.length} دروس</span></div>
      <div class="bar" style="margin:8px 0"><i style="width:${(exDone / exReady.length) * 100}%;background:var(--orange, var(--blue))"></i></div>
      <small class="muted">${exNext ? `${exDone ? 'كمّل' : 'ابدأ'}: ${esc(exNext.title_ar)}` : 'خلصت الدروس المتاحة'}</small></a>` : ''}
    <a class="card" style="display:block;text-decoration:none;color:inherit" href="#/review"><b>🔁 مراجعة المصطلحات</b><p class="muted" style="margin:0">ثبّت اللي تعلمته بتكرار متباعد.</p></a>
    <div class="card">
      <span class="badge">🔤 مصطلح اليوم</span>
      <h2 class="en">${esc(wod.en)}</h2>
      <p>${esc(wod.ar)} — ${esc(wod.simple)}</p>
      <button class="btn ghost small" data-term="${wod.id}">التفاصيل والنطق</button>
    </div>`;
}

function path() {
  const next = nextLesson(pathTrack);
  const tabs = `<div class="seg" style="margin-bottom:14px">${Object.entries(TRACKS).map(([k, [ic, name]]) => `<button data-track="${k}" class="${pathTrack === k ? 'on' : ''}">${ic} ${name}</button>`).join('')}</div>`;
  view.innerHTML = `<h1>🗺️ مسار التعلم</h1>${tabs}` + curriculum.levels.filter((l) => l.track === pathTrack).map((l) => `
    <section class="level">
      <div class="level-head" style="background:${l.color}">
        <small>${levelLabel(l)}</small>
        <h2 style="margin:0">${l.icon} ${esc(l.title_ar)}</h2>
        <small class="en">${esc(l.title_en)}</small>
        <small style="float:left">${l.modules.filter((m) => isDone(m.id)).length}/${l.modules.length}</small>
      </div>
      ${l.modules.map((m) => {
        if (!m.ready) return `<div class="mod locked"><span class="dot">⏳</span><span><b>${esc(m.title_ar)}</b><br><small class="muted en">${esc(m.title_en)}</small><br><small class="muted">قريباً</small></span></div>`;
        if (!isUnlocked(m.id)) return `<div class="mod locked"><span class="dot">🔒</span><span><b>${esc(m.title_ar)}</b><br><small class="muted en">${esc(m.title_en)}</small><br><small class="muted">${overCap(m.id) ? 'هذا المستوى غير مفتوح لك حالياً (حدده المشرف)' : 'أكمل الدرس السابق لفتحه'}</small></span></div>`;
        const cls = isDone(m.id) ? 'done' : next?.id === m.id ? 'next' : '';
        return `<a class="mod ${cls}" href="#/lesson/${m.id}">
          <span class="dot">${isDone(m.id) ? '✓' : '▶'}</span>
          <span><b>${esc(m.title_ar)}</b><br><small class="muted en">${esc(m.title_en)}</small></span>
        </a>`;
      }).join('')}
    </section>`).join('');
  view.querySelectorAll('[data-track]').forEach((b) => b.addEventListener('click', () => { pathTrack = b.dataset.track; path(); }));
}

async function lesson(id, query) {
  const startAt = query && /s=(\d+)/.test(query) ? +query.match(/s=(\d+)/)[1] : null;
  const meta = lessonsOf().find((m) => m.id === id);
  if (!meta?.ready) return (location.hash = '#/path');
  if (me()) await sync.refresh(); // نتأكد أن المشرف ما أوقف التدريب أو غيّر السقف
  if (isSuspended()) return suspendedScreen();
  if (overCap(id)) {
    view.innerHTML = `<div class="card result"><div class="big">🔒</div><h2>هذا المستوى غير مفتوح لك</h2><p>حدد المشرف أقصى مستوى لك حالياً. تواصل معه لفتحه.</p><a class="btn" href="#/path">المسار</a></div>`;
    return;
  }
  if (!isUnlocked(id)) {
    const p = prevOf(id), nx = nextLesson(trackOf(id));
    view.innerHTML = `<div class="card result"><div class="big">🔒</div><h2>هذا الدرس مقفل</h2>
      <p>أكمل أول <b>${esc(p.title_ar)}</b> عشان ينفتح.</p>
      <a class="btn" href="#/lesson/${nx ? nx.id : p.id}">${nx ? 'كمّل من وين وقفت' : 'ارجع للدرس السابق'}</a><br><a class="btn ghost" href="#/path">المسار</a></div>`;
    return;
  }
  try {
    const data = await (await fetch(`content/lessons/${id}.json`)).json();
    const list = readyList(trackOf(id)), i = list.findIndex((m) => m.id === id);
    playLesson(view, { id, ...data }, (go) => { location.hash = go || '#/path'; }, startAt, list[i + 1]?.id || null);
  } catch {
    view.innerHTML = `<div class="card"><h2>ما قدرنا نحمّل الدرس 😕</h2><p>تأكد من الاتصال وجرّب مرة ثانية.</p><a class="btn" href="#/path">رجوع</a></div>`;
  }
}

function glossary() {
  const items = allTerms().sort((a, b) => a.en.localeCompare(b.en));
  view.innerHTML = `
    <h1>🔤 القاموس</h1>
    <input class="search" id="q" type="search" placeholder="ابحث بالعربي أو الإنجليزي..." autocomplete="off">
    <div id="list"></div>`;
  const list = view.querySelector('#list');
  const draw = (q = '') => {
    const n = q.trim().toLowerCase();
    const hit = items.filter((t) => !n || t.en.toLowerCase().includes(n) || t.ar.includes(n) || t.say.includes(n));
    list.innerHTML = hit.map((t) => `<button class="gl-item" data-term="${t.id}"><b class="en">${esc(t.en)}</b> · ${esc(t.ar)}</button>`).join('')
      || '<p class="muted">ما لقينا شي.</p>';
  };
  view.querySelector('#q').addEventListener('input', (e) => draw(e.target.value));
  draw();
}

// ---- المختبر: كل التمارين العملية في مكان واحد ----
const LAB_NAMES = {
  code: ['🐍', 'اكتب كود Python', 'تمارين برمجة بفحص تلقائي'],
  predict: ['🔮', 'توقّع الناتج', 'وش يطبع هذا الكود؟'],
  parsons: ['🧩', 'رتّب أسطر الكود', 'رتّب الأسطر لتشتغل'],
  fillcode: ['✏️', 'أكمل الفراغ', 'أكمل الكود الناقص'],
  terminal: ['⌨️', 'سطر الأوامر', 'تدرّب على أوامر حقيقية في محاكي آمن'],
  phish: ['🎣', 'اكتشف المشبوه', 'رسائل تصيّد وسجلات وحزم شبكة'],
  passlab: ['🔐', 'مختبر الباسورد', 'قِس قوة الباسورد'],
  firewall: ['🧱', 'الجدار الناري', 'طبّق قواعد السماح والمنع'],
  webvuln: ['💉', 'ثغرات الويب', 'محاكاة SQLi وXSS والإصلاح'],
  cipher: ['🔑', 'أدوات التشفير', 'Caesar وBase64 وSHA-256'],
  ctf: ['🚩', 'تحديات CTF', 'ابحث عن الـ Flag'],
  scenario: ['🎭', 'سيناريوهات', 'قرارات متفرعة ونتائجها'],
  order: ['🔢', 'رتّب الخطوات', 'مراحل وطبقات وإجراءات'],
  match: ['🧩', 'طابق', 'ربط المفاهيم بتعريفاتها']
};
async function labs() {
  view.innerHTML = '<h1>🧪 المختبر</h1><p class="muted">كل التطبيقات العملية في المنصة. اضغط أي تمرين وتنقلك له مباشرة.</p><div id="lb"></div>';
  try {
    const data = await (await fetch('content/labs.json')).json();
    view.querySelector('#lb').innerHTML = Object.entries(LAB_NAMES).filter(([k]) => data[k]).map(([k, [ic, name, desc]]) => `
      <details class="card" ${k === 'terminal' ? 'open' : ''}>
        <summary><b>${ic} ${name}</b> <span class="muted">(${data[k].length})</span><br><small class="muted">${desc}</small></summary>
        ${data[k].map((l) => {
          if (!isUnlocked(l.id)) return `<div class="mod locked" style="margin:8px 0 0"><span class="dot">🔒</span><span><b>${esc(l.title)}</b><br><small class="muted">${esc(l.lesson)} · يفتح بعد إكمال الدرس السابق</small></span></div>`;
          return `<a class="mod" href="#/lesson/${l.id}?s=${l.slide}" style="margin:8px 0 0"><span class="dot">${ic}</span><span><b>${esc(l.title)}</b><br><small class="muted">${esc(l.lesson)}</small></span></a>`;
        }).join('')}
      </details>`).join('');
  } catch { view.querySelector('#lb').innerHTML = '<p class="muted">تعذّر تحميل قائمة المختبرات.</p>'; }
}

// ---- المراجعة: بطاقات المصطلحات بالتكرار المتباعد ----
async function review() {
  let tl = {};
  try { tl = await (await fetch('content/term_lessons.json')).json(); } catch { /* نكمل بدون ربط */ }
  const learned = new Set(Object.entries(store.get().lessons).filter(([, l]) => l.done).map(([id]) => id));
  const pool = Object.keys(tl).filter((t) => getTerm(t) && tl[t].some((l) => learned.has(l)));
  const due = pool.filter(store.isDue).sort(() => Math.random() - 0.5).slice(0, 10);
  if (!pool.length) { view.innerHTML = '<h1>🔁 المراجعة</h1><div class="card"><p>ما عندك مصطلحات للمراجعة بعد. أنهِ درساً وارجع هنا.</p><a class="btn" href="#/path">ابدأ درس</a></div>'; return; }
  if (!due.length) { view.innerHTML = '<h1>🔁 المراجعة</h1><div class="card"><div class="emoji" style="font-size:3rem;text-align:center">🎉</div><h2>خلصت مراجعة اليوم</h2><p class="muted">ارجع بكرة، أو تعلّم درساً جديداً لتزيد مصطلحاتك.</p><a class="btn" href="#/path">المسار</a></div>'; return; }
  let n = 0, known = 0;
  const show = () => {
    if (n >= due.length) { view.innerHTML = `<h1>🔁 المراجعة</h1><div class="card result"><div class="big">💪</div><h2>عرفت ${known} من ${due.length}</h2><p class="muted">اللي ما عرفته بيرجع لك قريباً.</p><a class="btn" href="#/">الرئيسية</a></div>`; return; }
    const id = due[n], t = getTerm(id);
    view.innerHTML = `<h1>🔁 المراجعة <small class="muted">${n + 1}/${due.length}</small></h1>
      <div class="card" style="text-align:center;padding:28px 16px"><div class="en" style="font-size:1.8rem;font-weight:800">${esc(t.en)}</div><p class="muted">النطق: ${esc(t.say)}</p>
      <button class="speak" data-say="${esc(t.en)}" aria-label="نطق">🔊</button>
      <div id="ans" hidden><hr><b>${esc(t.ar)}</b><p>${esc(t.simple)}</p></div></div>
      <button class="btn blue" id="flip">اعرض المعنى</button>
      <div id="g" class="row" hidden><button class="btn ghost" id="no">ما عرفتها</button><button class="btn" id="yes">عرفتها ✓</button></div>`;
    view.querySelector('#flip').addEventListener('click', (e) => { e.target.hidden = true; view.querySelector('#ans').hidden = false; view.querySelector('#g').hidden = false; });
    const grade = (ok) => { store.gradeTerm(id, ok); if (ok) known++; n++; show(); };
    view.querySelector('#yes').addEventListener('click', () => grade(true));
    view.querySelector('#no').addEventListener('click', () => grade(false));
  };
  show();
}

function profile() {
  const st = store.get();
  const seg = (key, opts) => `<div class="seg">${opts.map(([v, label]) =>
    `<button data-set="${key}" data-val="${v}" class="${String(st.settings[key]) === String(v) ? 'on' : ''}">${label}</button>`).join('')}</div>`;
  view.innerHTML = `
    <h1>👤 حسابي</h1>
    <div class="grid3">
      <div class="stat"><b>🔥 ${st.streak}</b><span>أيام متتالية</span></div>
      <div class="stat"><b>⭐ ${st.xp}</b><span>XP</span></div>
      <div class="stat"><b>🏅</b><span>${store.rank().name}</span></div>
    </div>
    <div class="card">
      <div class="setting"><span>المظهر</span>${seg('theme', [['auto', 'تلقائي'], ['light', '☀️'], ['dark', '🌙']])}</div>
      <div class="setting"><span>حجم الخط</span>${seg('font', [[0.9, 'أ-'], [1, 'أ'], [1.15, 'أ+']])}</div>
    </div>
    ${me()?.role === 'admin' ? '<a class="card" style="display:block;text-decoration:none;color:inherit;border-color:var(--blue)" href="#/admin"><b>🛠️ لوحة المشرف</b><p class="muted" style="margin:0">تابع المتدربين وتحكم بتدريبهم.</p></a>' : ''}
    ${isSuspended() ? `<div class="callout warn"><span class="label">⏸️ تدريبك موقوف</span>${esc(me().note || 'أوقف المشرف تدريبك مؤقتاً.')}</div>` : ''}
    ${sync.currentUser()
      ? `<div class="card"><b>👋 ${esc(me().name || me().username)}</b>${me().role === 'admin' ? ' <span class="badge">مشرف</span>' : ''}<p class="muted">تقدمك محفوظ في حسابك ويتزامن بين أجهزتك.${me().max_level != null ? ` · أقصى مستوى مفتوح لك: ${me().max_level}` : ''}</p><button class="btn ghost small" id="out">تسجيل الخروج</button></div>`
      : `<div class="card"><b>احفظ تقدمك</b><p class="muted">سجّل حساب عشان ما يضيع تقدمك وتكمل من أي جهاز.</p><a class="btn blue small" href="#/login">دخول / حساب جديد</a></div>`}
    <button class="btn ghost" id="reset">مسح التقدم من هذا الجهاز</button>`;
  view.querySelector('#out')?.addEventListener('click', async () => { await sync.signOut(); route(); });
  view.querySelectorAll('[data-set]').forEach((b) => b.addEventListener('click', () => {
    const v = b.dataset.val;
    store.setSetting(b.dataset.set, b.dataset.set === 'font' ? +v : v);
    profile();
  }));
  view.querySelector('#reset').addEventListener('click', () => {
    if (confirm('متأكد تبغى تمسح كل تقدمك؟')) { store.reset(); profile(); }
  });
}

async function login() {
  let mode = 'login';
  const info = await api.publicInfo(); // هل التسجيل الذاتي مفتوح؟ وهل المشرف ما أُنشئ بعد؟
  const canRegister = info.unavailable || info.registration_open !== false;
  const draw = (msg = '') => {
    view.innerHTML = `
      <h1>${mode === 'login' ? '🔑 تسجيل الدخول' : '✨ حساب جديد'}</h1>
      ${canRegister ? `<div class="seg" style="margin-bottom:14px">
        <button data-m="login" class="${mode === 'login' ? 'on' : ''}">دخول</button>
        <button data-m="register" class="${mode === 'register' ? 'on' : ''}">حساب جديد</button>
      </div>` : '<p class="muted">التسجيل الذاتي مغلق. حسابك ينشئه لك المشرف.</p>'}
      <form class="card" id="af">
        <label>اسم المستخدم <small class="muted">(إنجليزي، 3-30)</small></label>
        <input class="search en" name="u" dir="ltr" autocomplete="username" autocapitalize="off" required minlength="3" maxlength="30" pattern="[A-Za-z0-9_]+">
        <label>كلمة المرور <small class="muted">(8 أحرف على الأقل)</small></label>
        <input class="search en" name="p" type="password" dir="ltr" autocomplete="${mode === 'login' ? 'current-password' : 'new-password'}" required minlength="8" maxlength="128">
        <div id="msg" class="feedback no" ${msg ? '' : 'hidden'}>${esc(msg)}</div>
        <button class="btn" type="submit">${mode === 'login' ? 'دخول' : 'إنشاء الحساب'}</button>
      </form>
      <p class="muted">بدون حساب تقدر تتعلم عادي، لكن تقدمك بيبقى على هذا الجهاز فقط.</p>
      ${info.setup_needed ? '<p><a href="#/setup">🛠️ إنشاء حساب المشرف (أول مرة)</a></p>' : ''}`;
    view.querySelectorAll('[data-m]').forEach((b) => b.addEventListener('click', () => { mode = b.dataset.m; draw(); }));
    view.querySelector('#af').addEventListener('submit', async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      const btn = e.target.querySelector('button[type=submit]'); btn.disabled = true;
      const r = await sync.signIn(mode, f.get('u'), f.get('p'));
      if (r.user) return (location.hash = '#/profile');
      draw(r.unavailable ? 'الحسابات غير متاحة حالياً (تحتاج الاستضافة تدعم PHP).' : r.error || 'صار خطأ، جرّب مرة ثانية.');
    });
  };
  draw();
}

/* ---------------- الراوتر ---------------- */

const routes = [
  [/^#?\/?$/, 'home', home],
  [/^#\/path$/, 'path', path],
  [/^#\/lesson\/([\w.-]+)(?:\?(.*))?$/, null, lesson],
  [/^#\/labs$/, 'labs', labs],
  [/^#\/review$/, 'review', review],
  [/^#\/glossary$/, 'glossary', glossary],
  [/^#\/profile$/, 'profile', profile],
  [/^#\/login$/, 'profile', login],
  [/^#\/setup$/, 'profile', () => setupPage(view, () => { location.hash = '#/admin'; })],
  [/^#\/admin$/, 'admin', () => adminGuard(() => adminHome(view, curriculum))],
  [/^#\/admin\/u\/(\d+)$/, 'admin', (id) => adminGuard(() => adminUserPage(view, curriculum, id))]
];

// صفحات المشرف: ننتظر معرفة هوية المستخدم أول، وغير المشرف يرجع للرئيسية (والسيرفر يتحقق بكل طلب أيضاً)
let ready = Promise.resolve();
async function adminGuard(fn) {
  view.innerHTML = '<p class="muted">جاري التحميل...</p>';
  await ready;
  if (me()?.role !== 'admin') { location.hash = '#/'; return; }
  fn();
}

function route() {
  closeSheet();
  const hash = location.hash || '#/';
  view.classList.remove('in-lesson');
  for (const [re, tab, fn] of routes) {
    const m = hash.match(re);
    if (!m) continue;
    nav.querySelector('[data-tab=admin]').hidden = me()?.role !== 'admin';
    if (isSuspended() && tab !== 'profile') return suspendedScreen();
    nav.classList.toggle('hidden', !tab);
    nav.querySelectorAll('a').forEach((a) => a.classList.toggle('active', a.dataset.tab === tab));
    window.scrollTo(0, 0);
    return fn(m[1], m[2]);
  }
  location.hash = '#/';
}

async function start() {
  store.applySettings();
  initTermEvents();
  [curriculum] = await Promise.all([fetch('content/curriculum.json').then((r) => r.json()), loadGlossary()]);
  window.addEventListener('hashchange', route);
  ready = sync.init();
  route();
  ready.then(() => route()); // بعد المزامنة نحدّث الشاشة بالتقدم المدموج
  // نتابع قرارات المشرف (إيقاف، سقف، فتح) كل دقيقتين وعند الرجوع للتطبيق
  const recheck = async () => { if (me() && await sync.refresh()) route(); };
  setInterval(recheck, 120000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) recheck(); });
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});
}

start();
