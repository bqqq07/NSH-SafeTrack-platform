// نقطة البداية: الراوتر البسيط (hash) + الشاشات الرئيسية.
import * as store from './store.js';
import { loadGlossary, initTermEvents, allTerms, openTerm, esc, closeSheet } from './glossary.js';
import { playLesson } from './player.js';

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

async function lesson(id) {
  const meta = lessonsOf().find((m) => m.id === id);
  if (!meta?.ready) return (location.hash = '#/path');
  try {
    const data = await (await fetch(`content/lessons/${id}.json`)).json();
    playLesson(view, { id, ...data }, () => (location.hash = '#/path'));
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
    <p class="muted">تقدمك محفوظ على هذا الجهاز فقط. لو مسحت بيانات المتصفح بيضيع.</p>
    <button class="btn ghost" id="reset">مسح التقدم</button>`;
  view.querySelectorAll('[data-set]').forEach((b) => b.addEventListener('click', () => {
    const v = b.dataset.val;
    store.setSetting(b.dataset.set, b.dataset.set === 'font' ? +v : v);
    profile();
  }));
  view.querySelector('#reset').addEventListener('click', () => {
    if (confirm('متأكد تبغى تمسح كل تقدمك؟')) { store.reset(); profile(); }
  });
}

/* ---------------- الراوتر ---------------- */

const routes = [
  [/^#?\/?$/, 'home', home],
  [/^#\/path$/, 'path', path],
  [/^#\/lesson\/([\w.-]+)$/, null, lesson],
  [/^#\/glossary$/, 'glossary', glossary],
  [/^#\/profile$/, 'profile', profile]
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
    return fn(m[1]);
  }
  location.hash = '#/';
}

async function start() {
  store.applySettings();
  initTermEvents();
  [curriculum] = await Promise.all([fetch('content/curriculum.json').then((r) => r.json()), loadGlossary()]);
  window.addEventListener('hashchange', route);
  route();
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});
}

start();
