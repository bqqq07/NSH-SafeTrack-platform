// تواصل مع واجهة الحسابات (PHP). إذا الاستضافة ما تدعم PHP (مثلاً تشغيل محلي) نرجع unavailable ونكمل كضيف.
const BASE = 'api/index.php';

async function call(action, { method = 'GET', body, query = '' } = {}) {
  try {
    const res = await fetch(`${BASE}?a=${action}${query}`, {
      method,
      credentials: 'same-origin',
      headers: method === 'POST' ? { 'Content-Type': 'application/json', 'X-CA': '1' } : {},
      body: body ? JSON.stringify(body) : undefined
    });
    const type = res.headers.get('content-type') || '';
    if (!type.includes('application/json')) return { unavailable: true };
    return { status: res.status, ...(await res.json()) };
  } catch {
    return { unavailable: true }; // لا نت أو لا سيرفر
  }
}
const post = (action, body) => call(action, { method: 'POST', body });

export const publicInfo = () => call('public');
export const setupAdmin = (setup_key, username, password) => post('setup_admin', { setup_key, username, password });
export const me = () => call('me');
export const register = (username, password) => post('register', { username, password });
export const login = (username, password) => post('login', { username, password });
export const logout = () => post('logout');
export const pullProgress = () => call('progress');
export const pushProgress = (progress) => post('progress', { progress });

// لوحة المشرف
export const adminList = () => call('admin_list');
export const adminUser = (id) => call('admin_user', { query: `&id=${id}` });
export const adminUpdate = (id, fields) => post('admin_update', { id, ...fields });
export const adminPassword = (id, password) => post('admin_password', { id, password });
export const adminReset = (id, level = null) => post('admin_reset', { id, level });
export const adminCreate = (username, password, full_name) => post('admin_create', { username, password, full_name });
export const adminDelete = (id) => post('admin_delete', { id });
export const adminSettings = (fields) => post('admin_settings', fields);
export const adminBulk = (status, note = '', ids = 'all') => post('admin_bulk', { status, note, ids });
