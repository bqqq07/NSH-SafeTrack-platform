// نظام المصطلحات: قاموس واحد (glossary.json) يُستخدم في كل المنصة.
let terms = {};

export async function loadGlossary() {
  const res = await fetch('content/glossary.json');
  terms = await res.json();
}

export const getTerm = (id) => terms[id];
export const allTerms = () => Object.entries(terms).map(([id, t]) => ({ id, ...t }));

export const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// نحوّل نص الدرس إلى HTML آمن:
//   [[id]]   ← زر مصطلح يفتح التفاصيل
//   **نص**   ← خط عريض
//   `أمر`    ← كود داخل السطر
// plain=true: بدون أزرار (للاستخدام داخل أزرار ثانية مثل خيارات الأسئلة)
export function rich(text, plain = false) {
  // الكود داخل `` يُستخرج أولاً حتى لا تتحول النجوم فيه (مثل **kwargs) إلى خط عريض
  const codes = [];
  const src = esc(text).replace(/`(.+?)`/g, (_, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
  return src
    .replace(/\[\[([a-z0-9-]+)\]\]/g, (_, id) => {
      const t = terms[id];
      if (plain) return t ? `<span class="en">${esc(t.en)}</span>` : id;
      return t ? `<button class="term en" data-term="${id}">${esc(t.en)}</button>` : id;
    })
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/\u0000(\d+)\u0000/g, (_, i) => `<code class="en">${codes[i]}</code>`);
}

// نطق المصطلح بالإنجليزي (Web Speech API) — يعتمد على دعم الجهاز
export function speak(text) {
  if (!('speechSynthesis' in window)) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = 'en-US';
  u.rate = 0.85;
  speechSynthesis.speak(u);
}

const sheet = () => document.getElementById('sheet');
const overlay = () => document.getElementById('overlay');

export function closeSheet() {
  sheet().hidden = true;
  overlay().hidden = true;
}

export function openTerm(id) {
  const t = terms[id];
  if (!t) return;
  sheet().innerHTML = `
    <div class="row between">
      <div>
        <div class="en-big en">${esc(t.en)}</div>
        <div class="muted">النطق: ${esc(t.say)}</div>
      </div>
      <button class="speak" aria-label="نطق المصطلح" data-say="${esc(t.en)}">🔊</button>
    </div>
    <dl>
      <dt>بالعربي</dt><dd><b>${esc(t.ar)}</b></dd>
      <dt>المعنى ببساطة</dt><dd>${esc(t.simple)}</dd>
      <dt>مثال</dt><dd>${esc(t.example)}</dd>
    </dl>
    <button class="btn" data-close>تمام، فهمت</button>`;
  sheet().hidden = false;
  overlay().hidden = false;
}

// نربط الضغط على أي مصطلح في أي مكان بالصفحة (مرة وحدة فقط)
export function initTermEvents() {
  document.addEventListener('click', (e) => {
    const term = e.target.closest('[data-term]');
    if (term) return openTerm(term.dataset.term);
    const say = e.target.closest('[data-say]');
    if (say) return speak(say.dataset.say);
    if (e.target.closest('[data-close]') || e.target.id === 'overlay') closeSheet();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeSheet(); });
}
