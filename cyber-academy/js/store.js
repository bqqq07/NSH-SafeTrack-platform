// حفظ التقدم محلياً في المتصفح (بدون حساب). كل شي يتخزن في localStorage.
const KEY = 'cyber-academy.v1';

const DEFAULTS = {
  xp: 0,
  streak: 0,
  lastDay: null,           // آخر يوم تعلّم فيه (YYYY-MM-DD)
  lessons: {},             // { 'm0.1': { done, score, slide } }
  settings: { theme: 'auto', font: 1 }
};

// المستويات حسب نقاط الخبرة XP
export const RANKS = [
  { min: 0, name: 'مبتدئ' },
  { min: 100, name: 'متدرّب' },
  { min: 300, name: 'محترف' },
  { min: 700, name: 'خبير' }
];

let state = read();

function read() {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY)) || {};
    return { ...DEFAULTS, ...saved, settings: { ...DEFAULTS.settings, ...saved.settings } };
  } catch {
    // وضع التصفح الخاص أو تخزين ممنوع: نشتغل بالذاكرة فقط
    return structuredClone(DEFAULTS);
  }
}

function save() {
  try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* نتجاهل */ }
}

const dayStr = (d) => d.toLocaleDateString('en-CA'); // 2026-10-19

export const get = () => state;

export function rank() {
  return [...RANKS].reverse().find((r) => state.xp >= r.min);
}

// نحدّث سلسلة الأيام (Streak) عند أي نشاط تعلّم
function touchDay() {
  const today = dayStr(new Date());
  if (state.lastDay === today) return;
  const y = new Date(); y.setDate(y.getDate() - 1);
  state.streak = state.lastDay === dayStr(y) ? state.streak + 1 : 1;
  state.lastDay = today;
}

export function saveSlide(id, slide) {
  const l = (state.lessons[id] ||= { done: false, score: 0, slide: 0 });
  l.slide = slide;
  save();
}

// نرجع النقاط المكتسبة (أول مرة فقط عشان ما يتكرر الاستفادة)
export function completeLesson(id, correct, total) {
  const l = (state.lessons[id] ||= { done: false, score: 0, slide: 0 });
  const first = !l.done;
  const gained = first ? 20 + correct * 5 : 0;
  l.done = true;
  l.slide = 0;
  l.score = Math.max(l.score, total ? Math.round((correct / total) * 100) : 100);
  state.xp += gained;
  touchDay();
  save();
  return { gained, first };
}

export function setSetting(key, value) {
  state.settings[key] = value;
  save();
  applySettings();
}

export function applySettings() {
  const { theme, font } = state.settings;
  const root = document.documentElement;
  if (theme === 'auto') root.removeAttribute('data-theme'); else root.dataset.theme = theme;
  root.style.setProperty('--fs', font);
}

export function reset() {
  state = structuredClone(DEFAULTS);
  save();
  applySettings();
}
