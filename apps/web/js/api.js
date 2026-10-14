/* 同源 API 客户端：只访问相对路径 /api/v1/* 与 /auth/*；带 CSRF；超时；错误结构化（含 requestId）。 */
(function () {
  'use strict';
  let csrf = null;

  class ApiError extends Error {
    constructor(status, code, message, requestId, details) {
      super(message); this.status = status; this.code = code; this.requestId = requestId; this.details = details || {};
    }
  }

  function readCsrfCookie() {
    const m = document.cookie.match(/(?:^|;\s*)kl_csrf=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : null;
  }

  async function request(method, path, { body, headers = {}, timeoutMs = 35000 } = {}) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeoutMs);
    const h = { Accept: 'application/json', ...headers };
    if (body !== undefined) h['Content-Type'] = 'application/json';
    if (method !== 'GET') h['X-CSRF-Token'] = csrf || readCsrfCookie() || '';
    let r;
    try {
      r = await fetch(path, { method, headers: h, body: body !== undefined ? JSON.stringify(body) : undefined,
        credentials: 'same-origin', signal: ctrl.signal, cache: 'no-store' });
    } catch (e) {
      throw new ApiError(0, e.name === 'AbortError' ? 'client_timeout' : 'network_error',
        e.name === 'AbortError' ? '请求超时，请检查网络后重试' : '网络连接失败，请检查网络后重试', null);
    } finally { clearTimeout(t); }
    const rid = r.headers.get('X-Request-Id');
    let json = null;
    try { json = await r.json(); } catch (e) { json = null; }
    if (!r.ok) {
      const err = (json && json.error) || {};
      if (r.status === 401 && path.startsWith('/api/')) window.dispatchEvent(new CustomEvent('kl:unauthenticated'));
      throw new ApiError(r.status, err.code || 'http_' + r.status, err.message || `请求失败（HTTP ${r.status}）`,
        (json && json.requestId) || rid, err.details);
    }
    return { data: json, etag: r.headers.get('ETag'), requestId: rid };
  }

  const API = {
    ApiError,
    setCsrf(v) { csrf = v; },
    async session() { const { data } = await request('GET', '/auth/session'); csrf = data.csrf; return data; },
    async login(username, password) {
      const { data } = await request('POST', '/auth/login', { body: { username, password } }); csrf = data.csrf; return data;
    },
    async logout() { await request('POST', '/auth/logout', { body: {} }); csrf = null; },
    get: (p, o) => request('GET', p, o),
    post: (p, body, o) => request('POST', p, { ...(o || {}), body }),
    put: (p, body, o) => request('PUT', p, { ...(o || {}), body }),
    patch: (p, body, o) => request('PATCH', p, { ...(o || {}), body }),
    del: (p, o) => request('DELETE', p, { ...(o || {}), body: {} }),
    opId() {
      const a = new Uint8Array(12); crypto.getRandomValues(a);
      return 'w' + Array.from(a, (b) => b.toString(16).padStart(2, '0')).join('');
    },
  };
  window.API = API;
})();
