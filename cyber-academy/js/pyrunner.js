// واجهة تشغيل Python من الصفحة. المحرك يتحمّل مرة وحدة (كسول) ويبقى في Worker.
let worker = null;
let ready = null;
let seq = 0;
const pending = new Map();

function rejectAll(err) {
  for (const p of pending.values()) p.reject(err);
  pending.clear();
}

function send(type, payload) {
  return new Promise((resolve, reject) => {
    const id = ++seq;
    pending.set(id, { resolve, reject });
    worker.postMessage({ id, type, payload });
  });
}

function spawn() {
  worker = new Worker(new URL('./pyworker.js', import.meta.url), { type: 'module' });
  worker.onmessage = ({ data }) => {
    const p = pending.get(data.id);
    if (!p) return;
    pending.delete(data.id);
    if (data.ok) p.resolve(data.result); else p.reject(new Error(data.error));
  };
  worker.onerror = (e) => { rejectAll(new Error(e.message || 'worker error')); reset(); };
  ready = send('init');
  // لو فشل التحميل نصفّر الحالة عشان المحاولة التالية تبدأ من جديد
  ready.catch(() => reset());
}

function reset() {
  try { worker?.terminate(); } catch { /* نتجاهل */ }
  worker = null; ready = null;
}

export const isSupported = () => typeof Worker !== 'undefined' && typeof WebAssembly !== 'undefined';

// نبدأ تحميل المحرك بدون تشغيل شي (مثلاً أول ما يفتح المتعلم درس برمجة)
export function warmup() {
  if (!worker && isSupported()) spawn();
  return ready;
}

// يشغّل الكود ويرجع النتيجة. لو تجاوز المهلة نوقف الـ Worker ونرجع { timeout: true }.
export async function execute(payload, { timeoutMs = 6000 } = {}) {
  if (!isSupported()) throw new Error('unsupported');
  if (!worker) spawn();
  await ready;
  const job = send('run', payload);
  let timer;
  const timeout = new Promise((resolve) => {
    timer = setTimeout(() => { rejectAll(new Error('timeout')); reset(); resolve({ timeout: true }); }, timeoutMs);
  });
  try {
    return await Promise.race([job, timeout]);
  } catch (e) {
    if (e.message === 'timeout') return { timeout: true };
    throw e;
  } finally {
    clearTimeout(timer);
  }
}
