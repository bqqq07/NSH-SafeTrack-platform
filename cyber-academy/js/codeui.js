// واجهة تمارين البرمجة: محرر مناسب للجوال + أنواع الشرائح (code / predict / parsons / fillcode).
// تشغيل بايثون يتم كسولاً (أول مرة بس) عبر pyrunner.js داخل Worker.
import { rich, esc } from './glossary.js';
import { explain, explainTimeout } from './pyerrors.js';

const LOADING = '⏳ جاري تجهيز محرك بايثون (حوالي 13MB، يتحمّل مرة وحدة ثم يشتغل بدون نت)...';
const TIMEOUT_MS = 6000;
const shuffle = (a) => { const b = [...a]; for (let i = b.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [b[i], b[j]] = [b[j], b[i]]; } return b; };
const lines = (v) => (Array.isArray(v) ? v : v ? [v] : []);

/* ---------- حفظ المسودة لكل تمرين ---------- */
const readDraft = (k) => { try { return localStorage.getItem(k); } catch { return null; } };
const writeDraft = (k, v) => { try { localStorage.setItem(k, v); } catch { /* نتجاهل */ } };

/* ---------- تشغيل بايثون بأمان ---------- */
// يرجع النتيجة أو { unsupported } أو { failed } أو { timeout }
async function runPy(payload, onLoading) {
  let mod;
  try { mod = await import('./pyrunner.js'); } catch { return { unsupported: true }; }
  if (!mod.isSupported()) return { unsupported: true };
  onLoading?.();
  try { return await mod.execute(payload, { timeoutMs: TIMEOUT_MS }); } catch (e) { return { failed: String(e.message || e) }; }
}

const FAIL_HTML = `<div class="callout warn"><span class="label">⚠️ تعذّر تشغيل بايثون</span>المحرك ما اشتغل على هذا الجهاز أو الاتصال. جرّب تحديث الصفحة. لو استمرت المشكلة تقدر تتخطى التمرين وتكمل.</div>`;

function renderRun(r) {
  if (r.timeout) return explainTimeout(TIMEOUT_MS / 1000);
  const out = r.stdout ? `<pre class="out" dir="ltr">${esc(r.stdout)}</pre>` : (r.error ? '' : '<p class="muted">(الكود اشتغل ولم يطبع شي)</p>');
  return out + (r.error ? explain(r.error) : '');
}

/* ---------- المحرر ---------- */
const SYMBOLS = [['⇥', '    ', 0, 'إزاحة'], ['⇤', 'DEDENT', 0, 'تقليل الإزاحة'], ['( )', '()', 1], [')', ')', 0], ['[ ]', '[]', 1], [']', ']', 0], ['{ }', '{}', 1], ['}', '}', 0],
  ['" "', '""', 1], ["'", "'", 0], [':', ':', 0], ['=', '=', 0], ['#', '#', 0], ['_', '_', 0], ['.', '.', 0], [',', ',', 0],
  ['+', '+', 0], ['-', '-', 0], ['*', '*', 0], ['/', '/', 0], ['%', '%', 0], ['<', '<', 0], ['>', '>', 0], ['!', '!', 0]];

export function createEditor(host, { value = '', onInput = () => {} } = {}) {
  host.innerHTML = `<div class="ed"><div class="gut" aria-hidden="true"></div>
    <textarea class="ta" dir="ltr" wrap="off" spellcheck="false" autocapitalize="off" autocomplete="off" autocorrect="off" aria-label="محرر الكود"></textarea></div>
    <div class="symbar" role="toolbar" aria-label="رموز سريعة">${SYMBOLS.map(([l, , , t], i) => `<button type="button" data-i="${i}" ${t ? `title="${t}"` : ''}>${esc(l)}</button>`).join('')}</div>`;
  const ta = host.querySelector('.ta'), gut = host.querySelector('.gut');
  let errLine = null;

  const draw = () => {
    const n = ta.value.split('\n').length;
    gut.innerHTML = Array.from({ length: n }, (_, i) => `<div class="${errLine === i + 1 ? 'bad' : ''}">${i + 1}</div>`).join('');
    ta.rows = Math.min(Math.max(n, 5), 14);
    gut.style.maxHeight = `${ta.clientHeight || 0}px`;
    gut.scrollTop = ta.scrollTop;
  };
  const changed = () => { draw(); onInput(ta.value); };
  const insert = (text, back = 0) => {
    ta.focus();
    const s = ta.selectionStart, e = ta.selectionEnd;
    ta.setRangeText(text, s, e, 'end');
    ta.selectionStart = ta.selectionEnd = s + text.length - back;
    changed();
  };
  const dedent = () => {
    const s = ta.selectionStart, v = ta.value;
    const ls = v.lastIndexOf('\n', s - 1) + 1;
    const m = v.slice(ls).match(/^ {1,4}/);
    if (!m) return;
    ta.setRangeText('', ls, ls + m[0].length, 'preserve');
    ta.selectionStart = ta.selectionEnd = Math.max(ls, s - m[0].length);
    changed();
  };
  const newline = () => {
    const s = ta.selectionStart, v = ta.value;
    const ls = v.lastIndexOf('\n', s - 1) + 1;
    const before = v.slice(ls, s);
    const indent = before.match(/^ */)[0].length + (before.trimEnd().endsWith(':') ? 4 : 0);
    insert('\n' + ' '.repeat(indent));
  };

  // أزرار الرموز: pointerdown + preventDefault عشان الكيبورد ما ينسحب
  host.querySelector('.symbar').addEventListener('pointerdown', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    e.preventDefault();
    const [, ins, back] = SYMBOLS[+b.dataset.i];
    if (ins === 'DEDENT') dedent(); else insert(ins, back);
  });
  ta.addEventListener('keydown', (e) => {
    if (e.key === 'Tab') { e.preventDefault(); e.shiftKey ? dedent() : insert('    '); }
  });
  ta.addEventListener('beforeinput', (e) => {
    if (e.inputType === 'insertLineBreak' || e.inputType === 'insertParagraph') { e.preventDefault(); newline(); }
  });
  ta.addEventListener('input', changed);
  ta.addEventListener('scroll', () => { gut.scrollTop = ta.scrollTop; });

  ta.value = value;
  draw();
  return {
    get: () => ta.value,
    set: (v) => { ta.value = v; errLine = null; changed(); },
    markLine: (n) => { errLine = n || null; draw(); },
    focus: () => ta.focus()
  };
}

/* ---------- شريحة code (اكتب الحل / أصلح الخطأ) ---------- */
export function renderCode(s, box, done, ctx) {
  const key = `ca.draft.${ctx.lesson.id}.${ctx.i}`;
  const bug = s.variant === 'bugfix';
  const visible = (s.tests || []).filter((t) => !t.hidden);
  const hiddenCount = (s.tests || []).length - visible.length;
  const needsInput = /\binput\s*\(/.test(s.starter || '') || /\binput\s*\(/.test(s.solution || '') || s.stdin_box;
  box.innerHTML = `
    <span class="badge lab">🐍 ${bug ? 'أصلح الخطأ' : 'اكتب الكود'}</span>
    <h2>${esc(s.title)}</h2>
    ${lines(s.instructions).map((p) => `<p>${rich(p)}</p>`).join('')}
    ${visible.length ? `<div class="card soft"><b>المطلوب يتحقق منه:</b><ul class="points">${visible.map((t) => `<li>${esc(t.label || (t.call ? `${t.call} ← ${JSON.stringify(t.expect)}` : 'الناتج'))}</li>`).join('')}${hiddenCount ? `<li class="muted">+ ${hiddenCount} اختبار مخفي</li>` : ''}</ul></div>` : ''}
    <div id="ed"></div>
    ${needsInput ? `<details class="stdin"><summary>⌨️ المدخلات لـ <code class="en">input()</code> (سطر لكل مدخل)</summary><textarea id="stdin" class="search en" dir="ltr" rows="2" placeholder="مثال:&#10;سارة&#10;25">${esc((s.stdin || []).join('\n'))}</textarea></details>` : ''}
    <div class="row" style="margin:10px 0"><button class="btn blue small" id="run">▶ تشغيل</button><button class="btn small" id="chk">✔ تحقق</button><button class="btn ghost small" id="rst" title="إعادة الكود الأصلي">↺</button></div>
    <div id="out"></div>
    <div class="row"><button class="btn ghost small" id="hint">💡 تلميح</button><button class="btn ghost small" id="sol" hidden>👁 الحل</button></div>
    <div id="hints"></div>`;
  const q = (x) => box.querySelector(x);
  const starter = s.starter || '';
  const ed = createEditor(q('#ed'), { value: readDraft(key) ?? starter, onInput: (v) => writeDraft(key, v) });
  const stdin = () => { if (!q('#stdin')) return s.stdin || []; const v = q('#stdin').value; return v === '' ? [] : v.replace(/\n$/, '').split('\n'); };
  let attempts = 0, hintN = 0, usedSolution = false, finished = false;
  const busy = (on) => box.querySelectorAll('#run, #chk').forEach((b) => (b.disabled = on));

  const show = (html) => { q('#out').innerHTML = html; };
  const loading = () => show(`<p class="muted">${LOADING}</p>`);

  q('#run').addEventListener('click', async () => {
    busy(true); show('<p class="muted">⏳ جاري التشغيل...</p>');
    const r = await runPy({ mode: 'run', code: ed.get(), stdin: stdin(), files: s.files }, loading);
    busy(false);
    if (r.unsupported || r.failed) return show(FAIL_HTML + skipBtn());
    ed.markLine(r.error?.line);
    show(renderRun(r));
  });

  q('#chk').addEventListener('click', async () => {
    busy(true); show('<p class="muted">⏳ جاري الفحص...</p>');
    const r = await runPy({ mode: 'tests', code: ed.get(), tests: s.tests, checks: s.checks, files: s.files }, loading);
    busy(false);
    if (r.unsupported || r.failed) return show(FAIL_HTML + skipBtn());
    if (r.timeout) return show(explainTimeout(TIMEOUT_MS / 1000));
    attempts++;
    const firstErr = r.results.find((x) => x.error)?.error;
    ed.markLine(firstErr?.line);
    const list = [...r.checks.map((c) => `<li class="${c.ok ? 'ok' : 'no'}">${c.ok ? '✅' : '❌'} ${esc(c.msg)}</li>`),
      ...r.results.map((t) => {
        if (t.ok) return `<li class="ok">✅ ${esc(t.label)}</li>`;
        if (t.hidden) return `<li class="no">❌ اختبار مخفي فشل: فكّر بالحالات الخاصة (صفر، فارغ، أرقام كبيرة...)</li>`;
        const det = t.error ? '' : `<div class="en muted" dir="ltr">${t.call ? `${esc(t.call)}<br>` : ''}${t.stdin ? `input: ${esc(JSON.stringify(t.stdin))}<br>` : ''}expected: ${esc(t.expected)}<br>got: ${esc(t.got ?? '')}</div>`;
        return `<li class="no">❌ ${esc(t.label)}${det}</li>`;
      })].join('');
    if (r.passed) {
      finished = true;
      ctx.results[`${ctx.i}:code`] = attempts <= 2 && !usedSolution;
      show(`<ul class="tlist">${list}</ul><div class="feedback ok"><b>🎉 ممتاز! الكود ينجح كل الاختبارات</b>${s.explain ? rich(s.explain) : ''}</div>`);
      return done();
    }
    show(`<ul class="tlist">${list}</ul>${firstErr ? explain(firstErr) : ''}`);
    if (attempts >= 2 && s.solution) q('#sol').hidden = false;
  });

  q('#rst').addEventListener('click', () => { if (confirm('ترجّع الكود الأصلي؟ تعديلاتك بتضيع.')) { ed.set(starter); writeDraft(key, starter); } });

  q('#hint').addEventListener('click', () => {
    const hs = s.hints || [];
    if (!hs.length) return;
    if (hintN < hs.length) hintN++;
    q('#hints').innerHTML = hs.slice(0, hintN).map((h, i) => `<div class="feedback no"><b>💡 تلميح ${i + 1}</b>${rich(h)}</div>`).join('');
    if (hintN >= hs.length && s.solution) q('#sol').hidden = false;
  });
  q('#sol').addEventListener('click', () => {
    usedSolution = true;
    q('#hints').innerHTML += `<div class="feedback"><b>👁 الحل</b><pre class="code" dir="ltr">${esc(s.solution)}</pre><button class="btn ghost small" id="cp">انسخه للمحرر</button></div>`;
    q('#cp').addEventListener('click', () => ed.set(s.solution));
    q('#sol').hidden = true;
  });

  // تخطي التمرين لو تعذّر تشغيل المحرك (ما نقفل على المتعلم)
  const skipBtn = () => '<button class="btn ghost small" id="skip">تخطي التمرين</button>';
  box.addEventListener('click', (e) => { if (e.target.id === 'skip' && !finished) { finished = true; ctx.results[`${ctx.i}:code`] = false; done(); } });
}

/* ---------- شريحة predict (توقّع الناتج) ---------- */
export function renderPredict(s, box, done, ctx) {
  const idx = shuffle(s.options.map((_, i) => i));
  const options = idx.map((i) => s.options[i]);
  const answer = idx.indexOf(s.answer);
  box.innerHTML = `
    <span class="badge lab">🔮 توقّع الناتج</span>
    <h2>${esc(s.title || 'وش بيطلع؟')}</h2>
    ${lines(s.intro).map((p) => `<p>${rich(p)}</p>`).join('')}
    <pre class="code" dir="ltr">${esc(s.code)}</pre>
    ${s.stdin ? `<p class="muted">المدخلات: <code class="en">${esc(s.stdin.join(', '))}</code></p>` : ''}
    <p><b>${esc(s.question || 'وش يطبع هذا الكود؟')}</b></p>
    <div class="opts">${options.map((o, i) => `<button class="opt" data-i="${i}"><pre class="inline-pre" dir="ltr">${esc(o)}</pre></button>`).join('')}</div>
    <div id="fb"></div><div id="out"></div>`;
  box.querySelectorAll('.opt').forEach((btn) => btn.addEventListener('click', () => {
    const ok = +btn.dataset.i === answer;
    box.querySelectorAll('.opt').forEach((b) => (b.disabled = true));
    btn.classList.add(ok ? 'right' : 'wrong');
    if (!ok) box.querySelector(`[data-i="${answer}"]`).classList.add('right');
    ctx.results[`${ctx.i}:predict`] = ok;
    box.querySelector('#fb').innerHTML = `<div class="feedback ${ok ? 'ok' : 'no'}"><b>${ok ? '🎉 توقعك صح!' : '🤔 مو بالضبط'}</b>${rich(s.why || '')}</div>
      <button class="btn blue small" id="runit">▶ شغّل الكود وشوف الناتج الحقيقي</button>`;
    done();
    box.querySelector('#runit').addEventListener('click', async (e) => {
      e.target.disabled = true;
      const out = box.querySelector('#out');
      out.innerHTML = `<p class="muted">${LOADING}</p>`;
      const r = await runPy({ mode: 'run', code: s.code, stdin: s.stdin }, null);
      out.innerHTML = r.unsupported || r.failed ? '<p class="muted">تعذّر التشغيل هنا، لكن توقعك محسوب.</p>' : renderRun(r);
    });
  }));
}

/* ---------- شريحة parsons (رتّب الأسطر) ---------- */
export function renderParsons(s, box, done, ctx) {
  // نخلط أولاً ثم نعطي كل سطر رقمه (الرقم = موقعه في المصفوفة) عشان blocks[id] يرجع السطر الصحيح
  const blocks = shuffle([...s.lines.map((t) => ({ t })), ...(s.distractors || []).map((t) => ({ t, extra: true }))]).map((b, i) => ({ ...b, id: i }));
  let placed = [], bad = -1, attempts = 0;
  box.innerHTML = `
    <span class="badge lab">🧩 رتّب الأسطر</span>
    <h2>${esc(s.title)}</h2>
    ${lines(s.intro).map((p) => `<p>${rich(p)}</p>`).join('')}
    <div class="pz-sol" id="sol"></div>
    <div class="pz-pool" id="pool"></div>
    <div class="row" style="margin:10px 0"><button class="btn small" id="chk">✔ تحقق</button><button class="btn ghost small" id="rst">↺ ابدأ من جديد</button></div>
    <div id="fb"></div>`;
  const blk = (b, extra = '') => `<button type="button" class="blk ${extra}" data-id="${b.id}"><pre dir="ltr">${esc(b.t)}</pre></button>`;
  const draw = () => {
    box.querySelector('#sol').innerHTML = placed.length ? placed.map((id, k) => blk(blocks[id], k === bad ? 'bad' : '')).join('') : '<span class="muted">اضغط الأسطر بالترتيب الصحيح هنا (واضغط السطر لإرجاعه)</span>';
    box.querySelector('#pool').innerHTML = blocks.filter((b) => !placed.includes(b.id)).map((b) => blk(b)).join('');
  };
  box.addEventListener('click', (e) => {
    const b = e.target.closest('.blk'); if (!b) return;
    const id = +b.dataset.id; bad = -1;
    if (placed.includes(id)) placed = placed.filter((x) => x !== id); else placed.push(id);
    box.querySelector('#fb').innerHTML = '';
    draw();
  });
  box.querySelector('#rst').addEventListener('click', () => { placed = []; bad = -1; box.querySelector('#fb').innerHTML = ''; draw(); });
  box.querySelector('#chk').addEventListener('click', () => {
    const got = placed.map((id) => blocks[id].t);
    const solutions = [s.lines, ...(s.alt || [])];
    attempts++;
    if (solutions.some((sol) => sol.length === got.length && sol.every((l, i) => l === got[i]))) {
      ctx.results[`${ctx.i}:parsons`] = attempts <= 2;
      box.querySelector('#chk').disabled = true;
      box.querySelector('#fb').innerHTML = `<div class="feedback ok"><b>🎉 ترتيب صحيح!</b>${s.explain ? rich(s.explain) : ''}</div>`;
      return done();
    }
    bad = got.findIndex((l, i) => l !== s.lines[i]);
    if (bad >= got.length) bad = -1;
    draw();
    box.querySelector('#fb').innerHTML = `<div class="feedback no"><b>🤔 مو بالضبط</b>${got.length < s.lines.length ? 'ناقص أسطر.' : bad >= 0 ? `السطر رقم ${bad + 1} ما هو في مكانه (معلّم بالأحمر).` : 'راجع الترتيب.'} فكّر: وش لازم يصير أول؟${s.hint ? ` ${esc(s.hint)}` : ''}</div>`;
  });
  draw();
}

/* ---------- شريحة fillcode (أكمل الفراغ) ---------- */
export function renderFillCode(s, box, done, ctx) {
  const parts = s.code.split('____');
  let attempts = 0;
  box.innerHTML = `
    <span class="badge lab">✏️ أكمل الفراغ</span>
    <h2>${esc(s.title)}</h2>
    ${lines(s.intro).map((p) => `<p>${rich(p)}</p>`).join('')}
    <div class="fillcode" dir="ltr">${parts.map((p, i) => esc(p) + (i < s.blanks.length ? `<input class="blank" data-i="${i}" size="${Math.max(4, String(lines(s.blanks[i].answer)[0]).length + 1)}" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" aria-label="الفراغ ${i + 1}">` : '')).join('')}</div>
    ${s.bank ? `<div class="bank">${s.bank.map((w) => `<button type="button" class="chipw" data-w="${esc(w)}">${esc(w)}</button>`).join('')}</div>` : ''}
    <div class="row" style="margin:10px 0"><button class="btn small" id="chk">✔ تحقق</button></div>
    <div id="fb"></div><div id="out"></div>`;
  const inputs = [...box.querySelectorAll('.blank')];
  box.querySelectorAll('.chipw').forEach((c) => c.addEventListener('click', () => {
    const target = inputs.find((i) => !i.value) || inputs[inputs.length - 1];
    target.value = c.dataset.w; target.classList.remove('bad');
  }));
  const filled = () => s.code.split('____').map((p, i) => p + (i < s.blanks.length ? inputs[i].value.trim() : '')).join('');
  box.querySelector('#chk').addEventListener('click', async () => {
    attempts++;
    let allOk = true;
    inputs.forEach((inp, i) => {
      const ok = lines(s.blanks[i].answer).includes(inp.value.trim());
      inp.classList.toggle('bad', !ok); inp.classList.toggle('good', ok);
      allOk &&= ok;
    });
    const fb = box.querySelector('#fb');
    if (!allOk) {
      fb.innerHTML = `<div class="feedback no"><b>🤔 فيه فراغ غلط</b>الفراغات الحمراء تحتاج مراجعة.${s.hint ? ` ${esc(s.hint)}` : ''}</div>`;
      return;
    }
    ctx.results[`${ctx.i}:fill`] = attempts <= 2;
    box.querySelector('#chk').disabled = true;
    fb.innerHTML = `<div class="feedback ok"><b>🎉 صح!</b>${s.explain ? rich(s.explain) : ''}</div>`;
    done();
    if (s.show_output) {
      box.querySelector('#out').innerHTML = `<p class="muted">${LOADING}</p>`;
      const r = await runPy({ mode: 'run', code: filled(), stdin: s.stdin }, null);
      box.querySelector('#out').innerHTML = r.unsupported || r.failed ? '' : `<b>الناتج:</b>${renderRun(r)}`;
    }
  });
}
