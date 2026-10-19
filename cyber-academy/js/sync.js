// المزامنة: التطبيق يشتغل محلياً دايماً، وإذا المستخدم مسجّل دخول ندمج تقدمه مع السيرفر.
import * as api from './api.js';
import * as store from './store.js';

let user = null;
let timer = null;

export const currentUser = () => user;

// نسحب من السيرفر، ندمج مع المحلي، وندفع النتيجة
export async function syncNow() {
  if (!user) return;
  const r = await api.pullProgress();
  if (r.unavailable) return;
  store.mergeRemote(r.progress);
  await api.pushProgress(store.snapshot());
}

export async function init() {
  const r = await api.me();
  user = r.user || null;
  store.onChange(() => {
    if (!user) return;
    clearTimeout(timer);
    timer = setTimeout(() => api.pushProgress(store.snapshot()), 800); // نجمع الحفظات القريبة
  });
  await syncNow();
  return r;
}

export async function signIn(mode, username, password) {
  const r = await (mode === 'register' ? api.register : api.login)(username, password);
  if (r.user) { user = r.user; await syncNow(); }
  return r;
}

export async function signOut() {
  await api.logout();
  user = null;
}
