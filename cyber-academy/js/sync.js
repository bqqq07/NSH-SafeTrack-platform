// المزامنة: التطبيق يشتغل محلياً دايماً، وإذا المستخدم مسجّل دخول ندمج تقدمه مع السيرفر.
import * as api from './api.js';
import * as store from './store.js';

let user = null;
let timer = null;

export const currentUser = () => user;

// نسحب من السيرفر، ندمج مع المحلي، وندفع النتيجة.
// إذا المشرف صفّر تقدمي (epoch تغيّر) نستبدل المحلي بحالة السيرفر.
export async function syncNow() {
  if (!user) return;
  const r = await api.pullProgress();
  if (r.unavailable) return;
  if (user.epoch !== store.get().epoch) { store.replaceProgress(r.progress, user.epoch); return; }
  store.mergeRemote(r.progress);
  if (user.status === 'active') await api.pushProgress(store.snapshot());
}

// نعيد قراءة حالتي من السيرفر ونرجع true فقط لو تغيّر شي يؤثر على التدريب (إيقاف، سقف، فتح الدروس، الدور)
const key = (u) => JSON.stringify(u && [u.status, u.max_level, u.free_access, u.role, u.note]);
export async function refresh() {
  if (!user) return false;
  const r = await api.me();
  if (r.unavailable) return false;
  const before = key(user);
  user = r.user || null;
  if (user && user.epoch !== store.get().epoch) await syncNow();
  return before !== key(user);
}

export async function setupAsAdmin(setupKey, username, password) {
  const r = await api.setupAdmin(setupKey, username, password);
  if (r.user) { user = r.user; store.adopt(user.username); }
  return r;
}

export async function init() {
  const r = await api.me();
  user = r.user || null;
  if (user) store.adopt(user.username);
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
  if (r.user) { user = r.user; store.adopt(user.username); await syncNow(); }
  return r;
}

export async function signOut() {
  await api.logout();
  user = null;
  store.wipe();
}
