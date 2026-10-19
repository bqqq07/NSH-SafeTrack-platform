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
        <div class="msg-b${s.mono ? ' mono' : ''}" ${s.mono ? 'dir="ltr"' : ''}>${s.parts.map((p, i) => p.bad !== undefined
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
      box.querySelector('#fb').innerHTML = `<div class="feedback ${ok ? 'ok' : 'no'}"><b>${ok ? (found ? '🎉 ممتاز! لقطتها كلها' : '👌 صح، ما فيه شي مكشوف هنا') : `لقيت ${found} من ${found + missed}${wrong ? ` وعلّمت ${wrong} سليم بالغلط` : ''}`}</b><ul class="points">${why.join('')}</ul></div>
        <button class="btn" id="nx">تمام</button>`;
      box.querySelector('#chk').style.display = 'none';
      box.querySelector('#nx').addEventListener('click', () => { done(); ctx.next(); });
    });
  },

  // ترتيب خطوات: يضغط العناصر بالترتيب الصحيح (مثل طبقات OSI أو مراحل الاستجابة)
  order(s, box, done, ctx) {
    const shuffled = [...s.items].map((t, i) => ({ t, i }));
    do { shuffled.sort(() => Math.random() - 0.5); } while (shuffled.every((x, k) => x.i === k) && shuffled.length > 1);
    let next = 0, mistakes = 0;
    box.innerHTML = `
      <span class="badge lab">🧪 تطبيق عملي · رتّب الخطوات</span>
      <h2>${esc(s.title)}</h2>
      <p>${rich(s.intro || 'اضغط العناصر بالترتيب الصحيح من الأول للآخر.')}</p>
      <ol class="seq" id="seq"></ol>
      <div class="opts" id="pool">${shuffled.map((x) => `<button class="opt" data-i="${x.i}">${rich(x.t, true)}</button>`).join('')}</div>
      <div id="fb"></div>`;
    const seq = box.querySelector('#seq');
    box.querySelectorAll('#pool .opt').forEach((btn) => btn.addEventListener('click', () => {
      if (+btn.dataset.i !== next) {
        mistakes++;
        btn.classList.add('wrong');
        setTimeout(() => btn.classList.remove('wrong'), 500);
        return;
      }
      seq.insertAdjacentHTML('beforeend', `<li>${rich(s.items[next], true)}</li>`);
      btn.remove();
      if (++next === s.items.length) {
        ctx.results[`${ctx.i}:order`] = mistakes === 0;
        box.querySelector('#fb').innerHTML = `<div class="feedback ${mistakes ? 'no' : 'ok'}"><b>${mistakes ? `🤔 رتبتها بعد ${mistakes} محاولات خاطئة` : '🎉 ترتيب ممتاز!'}</b>${s.why ? rich(s.why) : ''}</div>`;
        done();
      }
    }));
  },

  // مطابقة: اضغط عنصر من اليمين ثم ما يقابله من اليسار
  match(s, box, done, ctx) {
    const left = s.pairs.map(([l], i) => ({ t: l, i }));
    const right = s.pairs.map(([, r], i) => ({ t: r, i })).sort(() => Math.random() - 0.5);
    let pick = null, left_ = s.pairs.length, mistakes = 0;
    box.innerHTML = `
      <span class="badge lab">🧪 تطبيق عملي · طابق</span>
      <h2>${esc(s.title)}</h2>
      <p>${rich(s.intro || 'اضغط عنصر ثم اضغط اللي يناسبه.')}</p>
      <div class="match"><div>${left.map((x) => `<button class="opt" data-side="l" data-i="${x.i}">${rich(x.t, true)}</button>`).join('')}</div>
      <div>${right.map((x) => `<button class="opt" data-side="r" data-i="${x.i}">${rich(x.t, true)}</button>`).join('')}</div></div>
      <div id="fb"></div>`;
    box.querySelectorAll('.opt').forEach((btn) => btn.addEventListener('click', () => {
      if (btn.disabled) return;
      if (btn.dataset.side === 'l') {
        box.querySelectorAll('[data-side=l]').forEach((b) => b.classList.remove('sel'));
        pick = btn; btn.classList.add('sel'); return;
      }
      if (!pick) return;
      if (pick.dataset.i === btn.dataset.i) {
        [pick, btn].forEach((b) => { b.classList.remove('sel'); b.classList.add('right'); b.disabled = true; });
        pick = null;
        if (--left_ === 0) {
          ctx.results[`${ctx.i}:match`] = mistakes === 0;
          box.querySelector('#fb').innerHTML = `<div class="feedback ${mistakes ? 'no' : 'ok'}"><b>${mistakes ? `خلصتها بعد ${mistakes} محاولات خاطئة` : '🎉 كلها صح!'}</b>${s.why ? rich(s.why) : ''}</div>`;
          done();
        }
      } else {
        mistakes++;
        btn.classList.add('wrong'); setTimeout(() => btn.classList.remove('wrong'), 500);
      }
    }));
  },

  // أدوات التشفير: Caesar وBase64 وSHA-256 مع تحدي اختياري لفك رسالة
  cipher(s, box, done, ctx) {
    const tools = s.tools || ['caesar', 'base64', 'sha256'];
    const names = { caesar: 'Caesar', base64: 'Base64', sha256: 'SHA-256' };
    box.innerHTML = `
      <span class="badge lab">🧪 تطبيق عملي · Crypto Playground</span>
      <h2>${esc(s.title || 'جرّب التشفير بنفسك')}</h2>
      <p>${rich(s.intro || '')}</p>
      <div class="seg" id="tabs">${tools.map((t, k) => `<button data-t="${t}" class="${k ? '' : 'on'}">${names[t]}</button>`).join('')}</div>
      <div class="card" id="tool" style="margin-top:12px"></div>
      ${s.challenge ? `<div class="card soft"><b>🎯 التحدي</b><p>${rich(s.challenge.prompt)}</p><pre class="code" dir="ltr">${esc(s.challenge.cipher)}</pre>
        <input class="search en" id="ans" dir="ltr" autocomplete="off" placeholder="اكتب الجواب..."><div id="cfb"></div>
        <button class="btn ghost small" id="chint" type="button">💡 تلميح</button></div>` : ''}`;
    const tool = box.querySelector('#tool');
    const caesar = (str, k) => str.replace(/[a-z]/gi, (c) => { const b = c <= 'Z' ? 65 : 97; return String.fromCharCode(((c.charCodeAt(0) - b + k + 26 * 10) % 26) + b); });
    const b64enc = (x) => btoa(String.fromCharCode(...new TextEncoder().encode(x)));
    const b64dec = (x) => { try { return new TextDecoder().decode(Uint8Array.from(atob(x.trim()), (c) => c.charCodeAt(0))); } catch { return '⚠️ نص Base64 غير صالح'; } };
    const sha = async (x) => {
      if (!crypto.subtle) return 'المتصفح ما يدعم SHA-256 هنا (يحتاج اتصال آمن HTTPS)';
      const h = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(x));
      return [...new Uint8Array(h)].map((b) => b.toString(16).padStart(2, '0')).join('');
    };
    const draw = (t) => {
      const body = {
        caesar: `<input class="search en" id="in" dir="ltr" placeholder="Hello" autocomplete="off"><label>الإزاحة (Shift): <b id="kv">3</b></label><input type="range" id="k" min="1" max="25" value="3" style="width:100%">
          <div class="row"><button class="btn small" id="a">تشفير</button><button class="btn blue small" id="b">فك</button></div>`,
        base64: `<input class="search en" id="in" dir="ltr" placeholder="Hello" autocomplete="off"><div class="row"><button class="btn small" id="a">Encode</button><button class="btn blue small" id="b">Decode</button></div>`,
        sha256: `<input class="search en" id="in" dir="ltr" placeholder="Hello" autocomplete="off"><p class="muted">غيّر حرف واحد وشوف كيف تتغير البصمة بالكامل.</p>`
      }[t];
      tool.innerHTML = `${body}<pre class="code" dir="ltr" id="out" style="white-space:pre-wrap;word-break:break-all">—</pre>`;
      const inp = tool.querySelector('#in'), out = tool.querySelector('#out');
      if (t === 'caesar') {
        const k = tool.querySelector('#k'); k.addEventListener('input', () => (tool.querySelector('#kv').textContent = k.value));
        tool.querySelector('#a').addEventListener('click', () => (out.textContent = caesar(inp.value, +k.value)));
        tool.querySelector('#b').addEventListener('click', () => (out.textContent = caesar(inp.value, -k.value)));
      } else if (t === 'base64') {
        tool.querySelector('#a').addEventListener('click', () => (out.textContent = b64enc(inp.value)));
        tool.querySelector('#b').addEventListener('click', () => (out.textContent = b64dec(inp.value)));
      } else inp.addEventListener('input', async () => (out.textContent = inp.value ? await sha(inp.value) : '—'));
    };
    box.querySelectorAll('#tabs button').forEach((b) => b.addEventListener('click', () => {
      box.querySelectorAll('#tabs button').forEach((x) => x.classList.toggle('on', x === b)); draw(b.dataset.t);
    }));
    draw(tools[0]);
    if (!s.challenge) return done();
    box.querySelector('#chint').addEventListener('click', () => (box.querySelector('#cfb').innerHTML = `<div class="feedback no"><b>💡 تلميح</b>${rich(s.challenge.hint)}</div>`));
    const ans = box.querySelector('#ans');
    ans.addEventListener('input', () => {
      if (ans.value.trim().toLowerCase() === s.challenge.answer.toLowerCase()) {
        ctx.results[`${ctx.i}:cipher`] = true;
        box.querySelector('#cfb').innerHTML = `<div class="feedback ok"><b>🎉 فككتها!</b>${rich(s.challenge.why || '')}</div>`;
        ans.disabled = true; done();
      }
    });
  },

  // محاكاة ثغرات الويب داخل الصفحة فقط: ما فيه أي طلب أو تنفيذ حقيقي. يشوف الثغرة ثم الإصلاح.
  webvuln(s, box, done, ctx) {
    const sqli = s.mode === 'sqli';
    box.innerHTML = `
      <span class="badge lab">🧪 تطبيق عملي · محاكاة ثغرة ${sqli ? 'SQL Injection' : 'XSS'}</span>
      <h2>${esc(s.title)}</h2>
      <p>${rich(s.intro)}</p>
      <div class="callout info"><span class="label">🧪 بيئة محاكاة</span>كل شي هنا تمثيل داخل الصفحة، ما ينفَّذ على أي موقع أو سيرفر حقيقي.</div>
      <div class="card">
        ${sqli
          ? `<input class="search en" id="u" dir="ltr" placeholder="username" autocomplete="off"><input class="search en" id="p" dir="ltr" placeholder="password" autocomplete="off">`
          : `<input class="search en" id="u" dir="ltr" placeholder="اكتب تعليق..." autocomplete="off">`}
        <div class="row"><button class="btn small" id="go">${sqli ? 'دخول' : 'نشر التعليق'}</button><button class="btn ghost small" id="try" type="button">✍️ جرّب هذا</button></div>
        <label class="row" style="margin-top:10px"><input type="checkbox" id="sec"> 🛡️ فعّل الحماية (${sqli ? 'Prepared Statement' : 'Output Encoding'})</label>
      </div>
      <pre class="code" dir="ltr" id="q">—</pre><div id="res"></div>`;
    const $ = (x) => box.querySelector(x);
    let broke = false, blocked = false;
    $('#try').addEventListener('click', () => { $('#u').value = sqli ? "admin' --" : '<script>alert(1)</script>'; if (sqli) $('#p').value = 'x'; });
    $('#go').addEventListener('click', () => {
      const u = $('#u').value, pw = sqli ? $('#p').value : '';
      const evil = sqli ? /'/.test(u + pw) && /(--|#|\bor\b|\bunion\b)/i.test(u + pw) : /<\s*script|on\w+\s*=|<\s*img/i.test(u);
      const secure = $('#sec').checked;
      if (sqli) {
        $('#q').textContent = secure
          ? `SELECT * FROM users WHERE username = ? AND password = ?\nparams: [${JSON.stringify(u)}, ${JSON.stringify(pw)}]`
          : `SELECT * FROM users WHERE username = '${u}' AND password = '${pw}'`;
      } else {
        $('#q').textContent = secure ? `<p>${u.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</p>   <- الحرف < صار &lt;` : `<p>${u}</p>   <- الصفحة تحط النص كما هو`;
      }
      if (evil && !secure) {
        broke = true;
        $('#res').innerHTML = `<div class="feedback no"><b>🚨 ${sqli ? 'تجاوز تسجيل الدخول بدون باسورد!' : 'السكربت تنفّذ في متصفح كل زائر!'} (محاكاة)</b>${sqli
          ? 'الإدخال صار جزءاً من أمر SQL فغيّر معناه: علامة -- تلغي شرط الباسورد.' : 'المتصفح شاف الكود كأنه جزء من الصفحة فنفّذه. المهاجم يقدر يسرق الجلسات.'}</div>`;
      } else if (evil && secure) {
        blocked = true;
        $('#res').innerHTML = `<div class="feedback ok"><b>🛡️ فشل الهجوم</b>${sqli ? 'الاستعلام الجاهز يعامل الإدخال كبيانات فقط، مو كأمر.' : 'النص انعرض كحروف عادية بدون ما يتنفّذ.'}</div>`;
      } else {
        $('#res').innerHTML = `<div class="feedback">${sqli ? 'بيانات غير صحيحة. جرّب «جرّب هذا».' : 'تم نشر التعليق. جرّب «جرّب هذا».'}</div>`;
      }
      if (broke && blocked) { ctx.results[`${ctx.i}:vuln`] = true; done(); }
      else if (broke) $('#res').insertAdjacentHTML('beforeend', '<p class="muted">الآن فعّل خانة الحماية وأعد نفس المحاولة.</p>');
    });
  },

  // سيناريو متفرّع: قرارات ونتائج (مثل الاستجابة للحوادث)
  scenario(s, box, done, ctx) {
    const show = (id) => {
      const n = s.nodes[id];
      if (n.end) {
        box.innerHTML = `<span class="badge lab">🧪 سيناريو</span><h2>${esc(s.title)}</h2><div class="callout ${n.good === false ? 'stop' : 'tip'}"><span class="label">${n.good === false ? '💥' : '🏁'} النهاية</span>${rich(n.text)}</div>`;
        return done();
      }
      const choices = [...n.choices].sort(() => Math.random() - 0.5);
      box.innerHTML = `<span class="badge lab">🧪 سيناريو</span><h2>${esc(s.title)}</h2><p>${rich(n.text)}</p>
        <div class="opts">${choices.map((c, i) => `<button class="opt" data-i="${i}">${rich(c.t, true)}</button>`).join('')}</div><div id="fb"></div>`;
      box.querySelectorAll('.opt').forEach((b) => b.addEventListener('click', () => {
        const c = choices[+b.dataset.i];
        box.querySelectorAll('.opt').forEach((x) => (x.disabled = true));
        b.classList.add(c.good ? 'right' : 'wrong');
        ctx.results[`${ctx.i}:sc:${id}`] = !!c.good;
        box.querySelector('#fb').innerHTML = `<div class="feedback ${c.good ? 'ok' : 'no'}"><b>${c.good ? '👍 قرار سليم' : '⚠️ قرار خطر'}</b>${rich(c.why)}</div><button class="btn" id="nx">كمّل</button>`;
        box.querySelector('#nx').addEventListener('click', () => show(c.next));
      }));
    };
    show(s.start);
  },

  // تحديات CTF: أدخل الـ Flag بعد ما تحل اللغز
  ctf(s, box, done, ctx) {
    let n = 0;
    const show = () => {
      const c = s.challenges[n];
      box.innerHTML = `<span class="badge lab">🏁 Mini CTF · ${n + 1}/${s.challenges.length}</span><h2>${esc(c.title)}</h2><p>${rich(c.desc)}</p>
        ${c.code ? `<pre class="code" dir="ltr" style="white-space:pre-wrap;word-break:break-all">${esc(c.code)}</pre>` : ''}
        <input class="search en" id="flag" dir="ltr" autocomplete="off" placeholder="FLAG{...}"><div id="fb"></div>
        <div class="row"><button class="btn small" id="sub">تحقق</button><button class="btn ghost small" id="hint" type="button">💡 تلميح</button></div>`;
      box.querySelector('#hint').addEventListener('click', () => (box.querySelector('#fb').innerHTML = `<div class="feedback no"><b>💡 تلميح</b>${rich(c.hint)}</div>`));
      box.querySelector('#sub').addEventListener('click', () => {
        const v = box.querySelector('#flag').value.trim();
        if (v.toLowerCase() === c.flag.toLowerCase()) {
          ctx.results[`${ctx.i}:ctf${n}`] = true;
          box.querySelector('#fb').innerHTML = `<div class="feedback ok"><b>🚩 صح!</b>${rich(c.why || '')}</div><button class="btn" id="nx">${n + 1 < s.challenges.length ? 'التحدي التالي' : 'تمام'}</button>`;
          box.querySelector('#nx').addEventListener('click', () => { if (++n < s.challenges.length) show(); else done(); });
        } else box.querySelector('#fb').innerHTML = '<div class="feedback no"><b>مو صحيح</b>جرّب مرة ثانية أو استخدم التلميح.</div>';
      });
    };
    show();
  },

  // لعبة الجدار الناري: لكل طلب قرر سماح أو منع حسب القواعد
  firewall(s, box, done, ctx) {
    const rules = s.rules.join('\n');
    questionRunner({
      type: 'practice', intro: s.intro,
      questions: s.packets.map((p) => ({ q: 'وش قرار الجدار الناري لهذا الطلب؟', code: `${rules}\n\n▶ ${p.line}`, options: ['✅ سماح (Allow)', '⛔ منع (Block)'], answer: p.allow ? 0 : 1, why: p.why, fixed: true }))
    }, box, done, ctx);
  },

  summary(s, box, done) {
    box.innerHTML = `
      <div class="emoji">📝</div>
      <h2>ملخص الدرس</h2>
      <div class="card"><ul class="points">${s.points.map((p) => `<li>${rich(p)}</li>`).join('')}</ul></div>`;
    done();
  }
};

// نخلط ترتيب الخيارات عند العرض عشان ما تكون الإجابة الصحيحة دايماً في نفس المكان.
// ما نخلط إذا الأسئلة تشير لحروف (A/B) في الكود، أو إذا ترتيب الخيارات ثابت بطبيعته (fixed).
function shuffled(q) {
  if (q.fixed || (q.code && /^[A-D]\)/m.test(q.code))) return q;
  const idx = q.options.map((_, i) => i).sort(() => Math.random() - 0.5);
  return { ...q, options: idx.map((i) => q.options[i]), answer: idx.indexOf(q.answer) };
}

function questionRunner(s, box, done, ctx) {
  const lab = s.type === 'practice';
  let n = 0;

  const show = () => {
    const q = shuffled(s.questions[n]);
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

export function playLesson(view, lesson, onExit, startAt = null, nextId = null) {
  const saved = store.get().lessons[lesson.id];
  const ctx = { lesson, results: {}, correct: 0, total: 0, i: startAt !== null ? Math.min(startAt, lesson.slides.length - 1) : saved && !saved.done ? Math.min(saved.slide, lesson.slides.length - 1) : 0, next: () => go(1) };

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
    fwd.style.display = ['practice', 'quiz', 'terminal', 'phish', 'firewall'].includes(s.type) ? 'none' : '';
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
        ${nextId ? '<p class="muted">🔓 انفتح لك الدرس التالي</p><button class="btn" id="nxt">الدرس التالي</button><br>' : ''}
        <button class="btn ${nextId ? 'ghost' : ''}" id="out">${nextId ? 'المسار' : 'كمّل المسار'}</button>
      </div>`;
    view.querySelector('#out').addEventListener('click', () => onExit());
    view.querySelector('#nxt')?.addEventListener('click', () => onExit(`#/lesson/${nextId}`));
  }

  fwd.addEventListener('click', () => go(1));
  back.addEventListener('click', () => go(-1));
  draw();
}
