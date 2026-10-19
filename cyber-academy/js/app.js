// نقطة البداية: الراوتر البسيط (hash) + الشاشات الرئيسية.
import * as store from './store.js';
import { loadGlossary, initTermEvents, allTerms, getTerm, speak, esc, closeSheet } from './glossary.js';
import { playLesson } from './player.js';
import * as sync from './sync.js';

const view = document.getElementById('view');
const nav = document.getElementById('nav');
let curriculum;

const lessonsOf = () => curriculum.levels.flatMap((l) => l.modules.map((m) => ({ ...m, level: l })));
const isDone = (id) => !!store.get().lessons[id]?.done;
// الدرس التالي = أول درس جاهز ما انحل
const nextLesson = () => lessonsOf().find((m) => m.ready && !isDone(m.id));

/* ---------------- الشاشات ---------------- */

function home() {
  const st = store.get();
  const next = nextLesson();
  const all = lessonsOf();
  const ready = all.filter((m) => m.ready);
  const done = ready.filter((m) => isDone(m.id)).length;
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
      </a>` : `<div class="card"><h2>🎉 خلصت كل الدروس المتاحة</h2><p>دروس جديدة جاية قريب.</p></div>`}
    <div class="card soft">
      <div class="row between"><b>تقدمك</b><span class="muted">${done}/${ready.length} دروس</span></div>
      <div class="bar" style="margin-top:8px"><i style="width:${ready.length ? (done / ready.length) * 100 : 0}%"></i></div>
    </div>
    <a class="card" style="display:block;text-decoration:none;color:inherit" href="#/review"><b>🔁 مراجعة المصطلحات</b><p class="muted" style="margin:0">ثبّت اللي تعلمته بتكرار متباعد.</p></a>
    <div class="card">
      <span class="badge">🔤 مصطلح اليوم</span>
      <h2 class="en">${esc(wod.en)}</h2>
      <p>${esc(wod.ar)} — ${esc(wod.simple)}</p>
      <button class="btn ghost small" data-term="${wod.id}">التفاصيل والنطق</button>
    </div>`;
}

function path() {
  const next = nextLesson();
  view.innerHTML = `<h1>🗺️ مسار التعلم</h1>` + curriculum.levels.map((l) => `
    <section class="level">
      <div class="level-head" style="background:${l.color}">
        <small>المستوى ${l.id}</small>
        <h2 style="margin:0">${l.icon} ${esc(l.title_ar)}</h2>
        <small class="en">${esc(l.title_en)}</small>
      </div>
      ${l.modules.map((m) => {
        const cls = isDone(m.id) ? 'done' : !m.ready ? 'locked' : next?.id === m.id ? 'next' : '';
        const icon = isDone(m.id) ? '✓' : m.ready ? '▶' : '🔒';
        return `<a class="mod ${cls}" href="#/lesson/${m.id}">
          <span class="dot">${icon}</span>
          <span><b>${esc(m.title_ar)}</b><br><small class="muted en">${esc(m.title_en)}</small>${m.ready ? '' : '<br><small class="muted">قريباً</small>'}</span>
        </a>`;
      }).join('')}
    </section>`).join('');
}

async function lesson(id, query) {
  const startAt = query && /s=(\d+)/.test(query) ? +query.match(/s=(\d+)/)[1] : null;
  const meta = lessonsOf().find((m) => m.id === id);
  if (!meta?.ready) return (location.hash = '#/path');
  try {
    const data = await (await fetch(`content/lessons/${id}.json`)).json();
    playLesson(view, { id, ...data }, () => (location.hash = '#/path'), startAt);
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
          const m = lessonsOf().find((x) => x.id === l.id);
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
    ${sync.currentUser()
      ? `<div class="card"><b>👋 ${esc(sync.currentUser().username)}</b><p class="muted">تقدمك محفوظ في حسابك ويتزامن بين أجهزتك.</p><button class="btn ghost small" id="out">تسجيل الخروج</button></div>`
      : `<div class="card"><b>احفظ تقدمك</b><p class="muted">سجّل حساب عشان ما يضيع تقدمك وتكمل من أي جهاز.</p><a class="btn blue small" href="#/login">دخول / حساب جديد</a></div>`}
    <button class="btn ghost" id="reset">مسح التقدم من هذا الجهاز</button>`;
  view.querySelector('#out')?.addEventListener('click', async () => { await sync.signOut(); profile(); });
  view.querySelectorAll('[data-set]').forEach((b) => b.addEventListener('click', () => {
    const v = b.dataset.val;
    store.setSetting(b.dataset.set, b.dataset.set === 'font' ? +v : v);
    profile();
  }));
  view.querySelector('#reset').addEventListener('click', () => {
    if (confirm('متأكد تبغى تمسح كل تقدمك؟')) { store.reset(); profile(); }
  });
}

function login() {
  let mode = 'login';
  const draw = (msg = '') => {
    view.innerHTML = `
      <h1>${mode === 'login' ? '🔑 تسجيل الدخول' : '✨ حساب جديد'}</h1>
      <div class="seg" style="margin-bottom:14px">
        <button data-m="login" class="${mode === 'login' ? 'on' : ''}">دخول</button>
        <button data-m="register" class="${mode === 'register' ? 'on' : ''}">حساب جديد</button>
      </div>
      <form class="card" id="af">
        <label>اسم المستخدم <small class="muted">(إنجليزي، 3-30)</small></label>
        <input class="search en" name="u" dir="ltr" autocomplete="username" autocapitalize="off" required minlength="3" maxlength="30" pattern="[A-Za-z0-9_]+">
        <label>كلمة المرور <small class="muted">(8 أحرف على الأقل)</small></label>
        <input class="search en" name="p" type="password" dir="ltr" autocomplete="${mode === 'login' ? 'current-password' : 'new-password'}" required minlength="8" maxlength="128">
        <div id="msg" class="feedback no" ${msg ? '' : 'hidden'}>${esc(msg)}</div>
        <button class="btn" type="submit">${mode === 'login' ? 'دخول' : 'إنشاء الحساب'}</button>
      </form>
      <p class="muted">بدون حساب تقدر تتعلم عادي، لكن تقدمك بيبقى على هذا الجهاز فقط.</p>`;
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
  [/^#\/login$/, 'profile', login]
];

function route() {
  closeSheet();
  const hash = location.hash || '#/';
  view.classList.remove('in-lesson');
  for (const [re, tab, fn] of routes) {
    const m = hash.match(re);
    if (!m) continue;
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
  route();
  sync.init().then(() => route()); // بعد المزامنة نحدّث الشاشة بالتقدم المدموج
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});
}

start();
