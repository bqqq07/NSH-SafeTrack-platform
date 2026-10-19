// مشغّل الدرس: يعرض الدرس شرائح (Stories-style)، يحفظ مكان التوقف، ويحسب النتيجة.
import { rich, esc } from './glossary.js';
import * as store from './store.js';

const CALLOUT = {
  info: ['🧠', 'معلومة'],
  tip: ['💡', 'نصيحة'],
  warn: ['⚠️', 'تحذير'],
  stop: ['🚫', 'لا تسوي كذا']
};

// كل نوع شريحة له دالة ترسم المحتوى في box وتنادي done() لما يسمح بالانتقال للتالي.
// الشرائح التفاعلية تنادي done() بعد ما يخلص المتعلم، والبقية فوراً.
const renderers = {
  intro(s, box, done, ctx) {
    box.innerHTML = `
      <div class="emoji">${s.emoji || '🎯'}</div>
      <h1>${esc(ctx.lesson.title_ar)}</h1>
      <p class="muted en">${esc(ctx.lesson.title_en)} · ${ctx.lesson.duration_min} دقايق</p>
      <div class="callout tip"><span class="label">🎯 الهدف</span>${rich(s.goal)}</div>`;
    done();
  },

  text(s, box, done) {
    const [icon, label] = CALLOUT[s.callout] || [];
    box.innerHTML = `
      ${s.emoji ? `<div class="emoji">${s.emoji}</div>` : ''}
      ${s.layer ? `<span class="badge">${esc(s.layer)}</span>` : ''}
      <h2>${esc(s.title)}</h2>
      ${(s.body || []).map((p) => `<p>${rich(p)}</p>`).join('')}
      ${s.code ? `<pre class="code" dir="ltr">${esc(s.code)}</pre>` : ''}
      ${s.callout ? `<div class="callout ${s.callout}"><span class="label">${icon} ${label}</span>${rich(s.note)}</div>` : ''}`;
    done();
  },

  terms(s, box, done) {
    box.innerHTML = `
      <span class="badge">🔤 مصطلحات جديدة</span>
      <h2>${esc(s.title || 'مصطلحات الدرس')}</h2>
      <p class="muted">اضغط على أي مصطلح تشوف نطقه ومعناه.</p>
      ${s.ids.map((id) => `<p>${rich(`[[${id}]]`)}</p>`).join('')}`;
    done();
  },

  // تمرين أو اختبار: سؤال سؤال، مع شرح فوري ليش الجواب صح أو غلط
  practice: questionRunner,
  quiz: questionRunner,


  // محاكي سطر أوامر: المتعلم يكتب الأمر بنفسه والموقع يرد بمخرجات جاهزة (آمن 100%، ما ينفّذ شي حقيقي)
  terminal(s, box, done, ctx) {
    let n = 0;
    const norm = (v) => v.trim().replace(/\s+/g, ' ');
    box.innerHTML = `
      <span class="badge lab">🧪 تطبيق عملي · سطر الأوامر</span>
      <h2>${esc(s.title)}</h2>
      ${s.intro ? `<p>${rich(s.intro)}</p>` : ''}
      <div class="goal" id="goal"></div>
      <div class="shell" dir="ltr"><div id="log"></div>
        <form id="f"><span class="ps">${esc(s.prompt || '$')}</span><input id="cmd" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" aria-label="اكتب الأمر"></form>
      </div>
      <div id="fb"></div>
      <button class="btn ghost small" id="hint" type="button">💡 تلميح</button>`;
    const log = box.querySelector('#log'), input = box.querySelector('#cmd'), fb = box.querySelector('#fb');
    const task = () => s.tasks[n];
    const showGoal = () => { box.querySelector('#goal').innerHTML = `<b>المهمة ${n + 1}/${s.tasks.length}:</b> ${rich(task().goal)}`; fb.innerHTML = ''; };
    let tries = 0;
    box.querySelector('#hint').addEventListener('click', () => { fb.innerHTML = `<div class="feedback no"><b>💡 تلميح</b><span class="en">${esc(task().hint)}</span></div>`; });
    box.querySelector('#f').addEventListener('submit', (e) => {
      e.preventDefault();
      const v = norm(input.value); input.value = '';
      if (!v) return;
      const t = task();
      log.insertAdjacentHTML('beforeend', `<div><span class="ps">${esc(s.prompt || '$')}</span> ${esc(v)}</div>`);
      if (t.accept.includes(v)) {
        if (t.out) log.insertAdjacentHTML('beforeend', `<pre>${esc(t.out)}</pre>`);
        ctx.results[`${ctx.i}:${n}`] = tries === 0;
        tries = 0;
        fb.innerHTML = `<div class="feedback ok"><b>🎉 تمام!</b>${rich(t.why)}</div>`;
        if (++n < s.tasks.length) {
          fb.insertAdjacentHTML('beforeend', '<button class="btn" id="nx">المهمة التالية</button>');
          fb.querySelector('#nx').addEventListener('click', showGoal);
        } else {
          fb.insertAdjacentHTML('beforeend', '<button class="btn" id="nx">تمام</button>');
          fb.querySelector('#nx').addEventListener('click', () => { done(); ctx.next(); });
        }
        input.blur();
      } else {
        tries++;
        log.insertAdjacentHTML('beforeend', `<pre class="err">${esc(t.fail || 'الأمر غير صحيح، جرّب مرة ثانية.')}</pre>`);
      }
      log.scrollTop = log.scrollHeight;
    });
    showGoal();
  },

  // مقياس قوة الباسورد: كل الحساب داخل المتصفح، ما يُرسل أي شي لأي مكان
  passlab(s, box, done) {
    box.innerHTML = `
      <span class="badge lab">🧪 تطبيق عملي · Password Lab</span>
      <h2>${esc(s.title || 'جرّب بنفسك: وش قوة الباسورد؟')}</h2>
      <p>${rich(s.intro || 'اكتب باسورد للتجربة وشوف كم يحتاج وقت عشان ينكسر. وصّل للمستوى «قوي».')}</p>
      <div class="callout warn"><span class="label">⚠️ تحذير</span>لا تكتب باسوردك الحقيقي! هذا للتجربة فقط، وما يُرسل لأي مكان.</div>
      <input class="search en" id="pw" type="text" dir="ltr" autocomplete="off" placeholder="اكتب باسورد تجريبي...">
      <div class="bar"><i id="m"></i></div>
      <p><b id="lvl">—</b> <span class="muted" id="time"></span></p>
      <ul class="points" id="chk"></ul>
      <button class="btn ghost small" id="skip" type="button">تخطي</button>`;
    const COMMON = ['password', '123456', 'qwerty', 'admin', 'letmein', 'welcome', 'iloveyou', 'saudi', '12345678', 'abc123', '111111', '000000', 'ahmed', 'mohammed', 'محمد', 'أحمد'];
    const pw = box.querySelector('#pw');
    const calc = (v) => {
      const lower = v.toLowerCase();
      const has = { len: v.length >= 12, mix: /[a-z]/.test(v) && /[A-Z]/.test(v), num: /\d/.test(v), sym: /[^\w\s]/.test(v) };
      const common = COMMON.some((c) => lower.includes(c));
      const seq = /(.)\1{2,}|0123|1234|2345|3456|4567|5678|6789|abcd|qwer/i.test(v);
      let pool = 0;
      if (/[a-z]/.test(v)) pool += 26; if (/[A-Z]/.test(v)) pool += 26; if (/\d/.test(v)) pool += 10; if (/[^a-zA-Z0-9]/.test(v)) pool += 32;
      let bits = v.length * Math.log2(pool || 1);
      if (common) bits = Math.min(bits, 20);
      if (seq) bits *= 0.6;
      const secs = Math.pow(2, bits) / 1e10; // تخمين: 10 مليار محاولة بالثانية (هجوم Offline)
      return { has, common, seq, bits, secs };
    };
    const human = (x) => {
      if (x < 1) return 'أقل من ثانية';
      const units = [[31536000, 'سنة'], [86400, 'يوم'], [3600, 'ساعة'], [60, 'دقيقة'], [1, 'ثانية']];
      if (x > 31536000e6) return 'ملايين السنين';
      const [sec, name] = units.find(([s]) => x >= s);
      return `${Math.round(x / sec).toLocaleString('en')} ${name}`;
    };
    const upd = () => {
      const v = pw.value;
      const r = calc(v);
      const lvl = !v ? 0 : r.bits < 28 ? 1 : r.bits < 45 ? 2 : r.bits < 65 ? 3 : 4;
      const names = ['—', '😱 ضعيف جداً', '😬 ضعيف', '🙂 جيد', '💪 قوي'];
      const cols = ['var(--line)', 'var(--red)', 'var(--orange)', 'var(--blue)', 'var(--green)'];
      const m = box.querySelector('#m'); m.style.width = `${lvl * 25}%`; m.style.background = cols[lvl];
      box.querySelector('#lvl').textContent = names[lvl];
      box.querySelector('#time').textContent = v ? `يتكسّر خلال: ${human(r.secs)}` : '';
      const c = (ok, t) => `<li>${ok ? '✅' : '⬜'} ${t}</li>`;
      box.querySelector('#chk').innerHTML = v ? [
        c(r.has.len, '12 حرف أو أكثر (الطول أهم شي)'), c(r.has.mix, 'حروف كبيرة وصغيرة'), c(r.has.num, 'أرقام'), c(r.has.sym, 'رموز'),
        c(!r.common, 'ما فيه كلمة شائعة أو اسم'), c(!r.seq, 'ما فيه تكرار أو تسلسل (1234، aaa)')
      ].join('') : '';
      if (lvl >= 4) done();
    };
    pw.addEventListener('input', upd);
    // التخطي: نبلّغ المشغّل عبر حدث عشان ينتقل للشريحة التالية
    box.querySelector('#skip').addEventListener('click', () => box.dispatchEvent(new CustomEvent('skip')));
  },

  // اكتشاف التصيّد: المتعلم يضغط على الأجزاء المشبوهة في الرسالة ثم يتحقق
  phish(s, box, done, ctx) {
    box.innerHTML = `
      <span class="badge lab">🧪 تطبيق عملي · Spot the Phish</span>
      <h2>${esc(s.title || 'اكتشف المشبوه')}</h2>
      <p>${rich(s.intro || 'اضغط على كل جزء تحس إنه مشبوه، وبعدين اضغط «تحقق».')}</p>
      <div class="msg">
        <div class="msg-h"><b>${esc(s.channel || 'رسالة')}</b><span class="en">${esc(s.from)}</span></div>
        <div class="msg-b">${s.parts.map((p, i) => p.bad !== undefined
          ? `<span class="flag" data-i="${i}" role="button" tabindex="0">${esc(p.t)}</span>` : esc(p.t)).join('')}</div>
      </div>
      <div id="fb"></div>
      <button class="btn" id="chk">تحقق</button>`;
    const sel = new Set();
    box.querySelectorAll('.flag').forEach((el) => el.addEventListener('click', () => {
      if (box.querySelector('#chk').disabled) return;
      const i = +el.dataset.i; sel.has(i) ? sel.delete(i) : sel.add(i); el.classList.toggle('sel');
    }));
    box.querySelector('#chk').addEventListener('click', (e) => {
      e.target.disabled = true;
      let found = 0, missed = 0, wrong = 0; const why = [];
      box.querySelectorAll('.flag').forEach((el) => {
        const i = +el.dataset.i, p = s.parts[i], on = sel.has(i);
        el.classList.remove('sel');
        if (p.bad && on) { found++; el.classList.add('hit'); } else if (p.bad) { missed++; el.classList.add('miss'); } else if (on) { wrong++; el.classList.add('fp'); }
        if (p.bad) why.push(`<li><b>${esc(p.t)}</b>: ${rich(p.why)}</li>`);
        else if (on) why.push(`<li><b>${esc(p.t)}</b> (سليم): ${rich(p.why)}</li>`);
      });
      const ok = !missed && !wrong;
      ctx.results[`${ctx.i}:phish`] = ok;
      box.querySelector('#fb').innerHTML = `<div class="feedback ${ok ? 'ok' : 'no'}"><b>${ok ? '🎉 ممتاز! لقطتها كلها' : `لقيت ${found} من ${found + missed}${wrong ? ` وعلّمت ${wrong} سليم بالغلط` : ''}`}</b><ul class="points">${why.join('')}</ul></div>
        <button class="btn" id="nx">تمام</button>`;
      box.querySelector('#chk').style.display = 'none';
      box.querySelector('#nx').addEventListener('click', () => { done(); ctx.next(); });
    });
  },

  summary(s, box, done) {
    box.innerHTML = `
      <div class="emoji">📝</div>
      <h2>ملخص الدرس</h2>
      <div class="card"><ul class="points">${s.points.map((p) => `<li>${rich(p)}</li>`).join('')}</ul></div>`;
    done();
  }
};

function questionRunner(s, box, done, ctx) {
  const lab = s.type === 'practice';
  let n = 0;

  const show = () => {
    const q = s.questions[n];
    box.innerHTML = `
      <span class="badge ${lab ? 'lab' : ''}">${lab ? '🧪 تطبيق عملي' : '✅ اختبار سريع'} · ${n + 1}/${s.questions.length}</span>
      ${n === 0 && s.intro ? `<p class="muted">${rich(s.intro)}</p>` : ''}
      <h2>${rich(q.q)}</h2>
      ${q.code ? `<pre class="code" dir="ltr">${esc(q.code)}</pre>` : ''}
      <div class="opts">${q.options.map((o, i) => `<button class="opt" data-i="${i}">${rich(o, true)}</button>`).join('')}</div>
      <div id="fb"></div>`;

    box.querySelectorAll('.opt').forEach((btn) => btn.addEventListener('click', () => {
      const pick = +btn.dataset.i;
      const ok = pick === q.answer;
      // نقفل الأزرار بعد أول اختيار؛ أول محاولة هي اللي تنحسب
      box.querySelectorAll('.opt').forEach((b) => (b.disabled = true));
      btn.classList.add(ok ? 'right' : 'wrong');
      if (!ok) box.querySelector(`[data-i="${q.answer}"]`).classList.add('right');
      ctx.results[`${ctx.i}:${n}`] = ok; // آخر محاولة للسؤال هي اللي تنحسب لو رجع للشريحة
      box.querySelector('#fb').innerHTML = `
        <div class="feedback ${ok ? 'ok' : 'no'}"><b>${ok ? '🎉 أحسنت!' : '🤔 مو بالضبط'}</b>${rich(q.why)}</div>
        <button class="btn" id="nextQ">${n + 1 < s.questions.length ? 'السؤال التالي' : 'تمام'}</button>`;
      box.querySelector('#nextQ').addEventListener('click', () => {
        if (++n < s.questions.length) show(); else { done(); ctx.next(); }
      });
    }));
  };
  show();
  // الزر السفلي للدرس يبقى مقفل لين يخلص كل الأسئلة
}

export function playLesson(view, lesson, onExit) {
  const saved = store.get().lessons[lesson.id];
  const ctx = { lesson, results: {}, correct: 0, total: 0, i: saved && !saved.done ? Math.min(saved.slide, lesson.slides.length - 1) : 0, next: () => go(1) };

  view.classList.add('in-lesson');
  view.innerHTML = `
    <div class="lesson-top">
      <a class="x" href="#/path" aria-label="خروج">✕</a>
      <div class="bar"><i id="prog"></i></div>
    </div>
    <div id="slide" class="slide"></div>
    <div class="lesson-foot">
      <button class="btn ghost" id="back">السابق</button>
      <button class="btn" id="fwd">التالي</button>
    </div>`;

  const box = view.querySelector('#slide');
  box.addEventListener('skip', () => go(1));
  const fwd = view.querySelector('#fwd');
  const back = view.querySelector('#back');

  function draw() {
    const s = lesson.slides[ctx.i];
    view.querySelector('#prog').style.width = `${(ctx.i / lesson.slides.length) * 100}%`;
    back.style.visibility = ctx.i === 0 ? 'hidden' : 'visible';
    const last = ctx.i === lesson.slides.length - 1;
    fwd.textContent = last ? 'إنهاء الدرس 🎉' : 'التالي';
    fwd.disabled = true;
    box.style.animation = 'none'; box.offsetHeight; box.style.animation = ''; // نعيد الحركة
    window.scrollTo(0, 0);
    store.saveSlide(lesson.id, ctx.i);
    // الأسئلة تحسب النتيجة من جديد في كل مرة يمر عليها
    renderers[s.type](s, box, () => { fwd.disabled = false; }, ctx);
    // زر التالي داخل التمارين يخفي الزر السفلي لأنه عنده زره
    fwd.style.display = ['practice', 'quiz', 'terminal', 'phish'].includes(s.type) ? 'none' : '';
  }

  function go(d) {
    const j = ctx.i + d;
    if (j < 0) return;
    if (j >= lesson.slides.length) return finish();
    ctx.i = j;
    draw();
  }

  function finish() {
    const answers = Object.values(ctx.results);
    ctx.total = answers.length;
    ctx.correct = answers.filter(Boolean).length;
    const { gained, first } = store.completeLesson(lesson.id, ctx.correct, ctx.total);
    const pct = ctx.total ? Math.round((ctx.correct / ctx.total) * 100) : 100;
    view.classList.remove('in-lesson');
    view.innerHTML = `
      <div class="result">
        <div class="big">${pct >= 80 ? '🏆' : pct >= 50 ? '👏' : '💪'}</div>
        <h1>${pct >= 80 ? 'ممتاز!' : pct >= 50 ? 'شغل حلو!' : 'بداية طيبة!'}</h1>
        <p>خلصت درس <b>${esc(lesson.title_ar)}</b></p>
        <div class="chips">
          <span class="chip">⭐ +${gained} XP${first ? '' : ' (مراجعة)'}</span>
          <span class="chip">🎯 ${ctx.correct}/${ctx.total}</span>
          <span class="chip">🔥 ${store.get().streak} يوم</span>
        </div>
        ${pct < 80 ? '<p class="muted">تقدر تعيد الدرس بدون حد، التكرار يثبّت المعلومة.</p>' : ''}
        <button class="btn" id="out">كمّل المسار</button>
      </div>`;
    view.querySelector('#out').addEventListener('click', onExit);
  }

  fwd.addEventListener('click', () => go(1));
  back.addEventListener('click', () => go(-1));
  draw();
}
