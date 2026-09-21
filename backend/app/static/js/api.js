/* 接口封装：统一响应解包、Bearer Token、401 自动刷新 */
const TOKEN_KEY = 'exam_access_token';
const REFRESH_KEY = 'exam_refresh_token';
const USER_KEY = 'exam_user';
const REMEMBER_KEY = 'exam_remember';

/* 会话存储策略：
   - 勾选「记住我」→ localStorage（关闭浏览器后仍保持登录，12 小时）
   - 未勾选       → sessionStorage（仅当前标签页有效，关闭浏览器即失效，2 小时） */
function _read(key) {
  return sessionStorage.getItem(key) || localStorage.getItem(key) || '';
}

function _write(key, value, persist) {
  if (persist) {
    localStorage.setItem(key, value);
    sessionStorage.removeItem(key);
  } else {
    sessionStorage.setItem(key, value);
    localStorage.removeItem(key);
  }
}

function _drop(key) {
  localStorage.removeItem(key);
  sessionStorage.removeItem(key);
}

export const session = {
  get remember() { return _read(REMEMBER_KEY) !== '0'; },
  get token() { return _read(TOKEN_KEY); },
  get refreshToken() { return _read(REFRESH_KEY); },
  get user() {
    try { return JSON.parse(_read(USER_KEY) || 'null'); } catch (e) { return null; }
  },
  save(data) {
    const persist = data.remember === undefined ? true : !!data.remember;
    _write(REMEMBER_KEY, persist ? '1' : '0', persist);
    if (data.access_token) _write(TOKEN_KEY, data.access_token, persist);
    if (data.user) _write(USER_KEY, JSON.stringify(data.user), persist);
    if (data.refresh_token) {
      _write(REFRESH_KEY, data.refresh_token, persist);
    } else if (!persist) {
      // 未记住我：服务端不发放刷新令牌，清理历史残留
      _drop(REFRESH_KEY);
    }
  },
  setUser(u) { _write(USER_KEY, JSON.stringify(u), this.remember); },
  clear() {
    _drop(TOKEN_KEY);
    _drop(REFRESH_KEY);
    _drop(USER_KEY);
    _drop(REMEMBER_KEY);
  },
};

export class ApiError extends Error {
  constructor(message, code) { super(message); this.code = code; }
}

let refreshing = null;

async function refreshToken() {
  if (!session.refreshToken) return false;
  if (!refreshing) {
    refreshing = fetch('/api/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: session.refreshToken }),
    }).then(async (r) => {
      const j = await r.json().catch(() => null);
      if (r.ok && j && j.code === 0) { session.save(j.data); return true; }
      return false;
    }).catch(() => false).finally(() => { refreshing = null; });
  }
  return refreshing;
}

async function request(method, url, body, opt = {}) {
  const headers = {};
  if (body !== undefined && !(body instanceof FormData)) headers['Content-Type'] = 'application/json';
  if (session.token) headers['Authorization'] = 'Bearer ' + session.token;

  const res = await fetch(url, {
    method, headers,
    body: body === undefined ? undefined : (body instanceof FormData ? body : JSON.stringify(body)),
  });

  if (res.status === 401 && !opt._retry) {
    const okRefresh = await refreshToken();
    if (okRefresh) return request(method, url, body, { ...opt, _retry: true });
    session.clear();
    if (!location.hash.includes('/login')) location.hash = '#/login';
    throw new ApiError('登录已过期，请重新登录', 401);
  }

  const ct = res.headers.get('content-type') || '';
  if (!ct.includes('application/json')) {
    if (!res.ok) throw new ApiError('请求失败（' + res.status + '）', res.status);
    return res;
  }
  const json = await res.json();
  if (json.code !== 0) throw new ApiError(json.message || '请求失败', json.code);
  return json.data;
}

export const api = {
  get: (url) => request('GET', url),
  post: (url, body) => request('POST', url, body),
  put: (url, body) => request('PUT', url, body),
  del: (url) => request('DELETE', url),
  raw: (method, url, body) => request(method, url, body),
  async upload(url, formData) {
    const headers = {};
    if (session.token) headers['Authorization'] = 'Bearer ' + session.token;
    const res = await fetch(url, { method: 'POST', headers, body: formData });
    let json = null;
    try { json = await res.json(); } catch (e) { json = null; }
    if (!res.ok || !json || json.code !== 0) {
      throw new ApiError((json && json.message) || ('上传失败（' + res.status + '）'),
                         json ? json.code : res.status);
    }
    return { data: json.data, message: json.message };
  },
  /** 上传后拿回二进制（证件照接口直接返回图片流，不是 JSON）。 */
  async uploadBlob(url, formData) {
    const headers = {};
    if (session.token) headers['Authorization'] = 'Bearer ' + session.token;
    const res = await fetch(url, { method: 'POST', headers, body: formData });
    if (!res.ok) {
      let m = '处理失败（' + res.status + '）';
      try {
        const j = await res.json();
        if (j && j.message) m = j.message;
      } catch (e) { /* 非 JSON 错误体，沿用状态文案 */ }
      throw new ApiError(m, res.status);
    }
    return { blob: await res.blob(), headers: res.headers,
             type: res.headers.get('Content-Type') || '' };
  },
  async download(url, filename) {
    const res = await request('GET', url);
    const blob = await res.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename || 'export.xlsx';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  },
  async downloadPost(url, body, filename) {
    const headers = { 'Content-Type': 'application/json' };
    if (session.token) headers['Authorization'] = 'Bearer ' + session.token;
    const res = await fetch(url, { method: 'POST', headers, body: JSON.stringify(body) });
    if (!res.ok) {
      const j = await res.json().catch(() => ({ message: '导出失败' }));
      throw new ApiError(j.message || '导出失败', res.status);
    }
    const cd = res.headers.get('content-disposition') || '';
    let name = filename || 'export.xlsx';
    const m = /filename\*=utf-8''([^;]+)/i.exec(cd) || /filename="?([^";]+)"?/i.exec(cd);
    if (m) { try { name = decodeURIComponent(m[1]); } catch (e) { name = m[1]; } }
    const blob = await res.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  },
};

export const msg = {
  ok(t) { ElementPlus.ElMessage.success(t || '操作成功'); },
  err(t) { ElementPlus.ElMessage.error(t || '操作失败'); },
  warn(t) { ElementPlus.ElMessage.warning(t); },
  info(t) { ElementPlus.ElMessage.info(t); },
};

export function handleErr(e) {
  if (e && e.code === 401) return;
  msg.err(e && e.message ? e.message : '操作失败');
}
