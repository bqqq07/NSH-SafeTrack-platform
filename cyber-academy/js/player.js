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
    fwd.style.display = s.type === 'practice' || s.type === 'quiz' ? 'none' : '';
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
