// تواصل مع واجهة الحسابات (PHP). إذا الاستضافة ما تدعم PHP (مثلاً تشغيل محلي) نرجع null ونكمل كضيف.
const BASE = 'api/index.php';

async function call(action, { method = 'GET', body } = {}) {
  try {
    const res = await fetch(`${BASE}?a=${action}`, {
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

export const me = () => call('me');
export const register = (username, password) => call('register', { method: 'POST', body: { username, password } });
export const login = (username, password) => call('login', { method: 'POST', body: { username, password } });
export const logout = () => call('logout', { method: 'POST' });
export const pullProgress = () => call('progress');
export const pushProgress = (progress) => call('progress', { method: 'POST', body: { progress } });
