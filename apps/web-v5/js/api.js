/* V6 引擎客户端（V5 界面用）：令牌登录 + Authorization: Bearer；可跨域（引擎需在 CORS_ALLOWED_ORIGINS 里放行本网页来源）。
 * - 由 BFF 在 /v5/ 提供时，引擎地址 = 本站（location.origin）
 * - 单文件版：localStorage 里用户设置的地址 > <meta name="kline-engine"> 里写入的默认地址
 * 错误结构化：status / code / message / requestId，与 V6 网页一致。 */
(function () {
  'use strict';
  const LS_ENGINE = 'kl_v5_engine';
  const LS_TOKEN = 'kl_v5_token';

  class ApiError extends Error {
    constructor(status, code, message, requestId, details) {
      super(message); this.status = status; this.code = code; this.requestId = requestId; this.details = details || {};
    }
  }

  const ls = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* 隐私模式等：只在本次打开有效 */ } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* 忽略 */ } },
  };
  let memToken = null;   // localStorage 不可用时的兜底

  function servedByBff() {
    return /^https?:$/.test(location.protocol) && /^\/v5(\/|$)/.test(location.pathname);
  }
  function bakedEngine() {
    const m = document.querySelector('meta[name="kline-engine"]');
    return m ? (m.getAttribute('content') || '').trim() : '';
  }
  function normalize(url) {
    let u;
    try { u = new URL(String(url || '').trim()); } catch (e) { return null; }
    if (!['http:', 'https:'].includes(u.protocol)) return null;
    return u.origin + u.pathname.replace(/\/+$/, '');
  }
  function engine() {
    if (servedByBff()) return location.origin;
    return normalize(ls.get(LS_ENGINE)) || normalize(bakedEngine()) || '';
  }
  function setEngine(url) {
    const n = normalize(url);
    if (!n) throw new ApiError(0, 'bad_engine', '引擎地址必须是 http:// 或 https:// 开头的网址', null);
    if (n !== engine()) clearToken();
    ls.set(LS_ENGINE, n);
    return n;
  }

  function readToken() {
    let t = memToken;
    const raw = ls.get(LS_TOKEN);
    if (raw) { try { t = JSON.parse(raw); } catch (e) { t = null; } }
    if (!t || t.engine !== engine() || !t.token) return null;
    if (t.expiresAt && t.expiresAt * 1000 <= Date.now()) return null;
    return t;
  }
  function clearToken() { memToken = null; ls.del(LS_TOKEN); }

  function netError(e) {
    if (e && e.name === 'AbortError') return new ApiError(0, 'client_timeout', '请求超时，请检查网络后重试', null);
    const here = location.origin && location.origin !== 'null' ? location.origin : '（本地文件）';
    return new ApiError(0, 'network_error',
      `无法连接分析引擎 ${engine() || '（未设置）'}：网络不通，或本网页的来源 ${here} 不在引擎的 CORS_ALLOWED_ORIGINS 允许列表里`, null);
  }

  async function request(method, path, { body, headers = {}, timeoutMs = 35000, auth = true } = {}) {
    const base = engine();
    if (!base) throw new ApiError(0, 'no_engine', '还没有设置分析引擎地址', null);
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    const h = { Accept: 'application/json', ...headers };
    if (body !== undefined) h['Content-Type'] = 'application/json';
    if (auth) {
      const t = readToken();
      if (!t) { clearToken(); window.dispatchEvent(new CustomEvent('kl:unauthenticated')); throw new ApiError(401, 'unauthenticated', '请先登录', null); }
      h.Authorization = `Bearer ${t.token}`;
    }
    let r;
    try {
      r = await fetch(base + path, { method, headers: h, body: body !== undefined ? JSON.stringify(body) : undefined,
        credentials: 'omit', mode: 'cors', signal: ctrl.signal, cache: 'no-store', referrerPolicy: 'no-referrer' });
    } catch (e) {
      throw netError(e);
    } finally { clearTimeout(timer); }
    const rid = r.headers.get('X-Request-Id');
    let json = null;
    try { json = await r.json(); } catch (e) { json = null; }
    if (!r.ok) {
      const err = (json && json.error) || {};
      if (r.status === 401 && auth) { clearToken(); window.dispatchEvent(new CustomEvent('kl:unauthenticated')); }
      throw new ApiError(r.status, err.code || 'http_' + r.status, err.message || `请求失败（HTTP ${r.status}）`,
        (json && json.requestId) || rid, err.details);
    }
    return { data: json, etag: r.headers.get('ETag'), requestId: (json && json.requestId) || rid };
  }

  const API = {
    ApiError,
    servedByBff,
    engine,
    setEngine,
    hasToken: () => !!readToken(),
    async login(username, password) {
      const { data } = await request('POST', '/auth/token', { body: { username, password }, auth: false, timeoutMs: 20000 });
      const t = { engine: engine(), token: data.token, expiresAt: data.expiresAt };
      memToken = t;
      ls.set(LS_TOKEN, JSON.stringify(t));
      return data;
    },
    logout() { clearToken(); },
    // 形态图鉴数据（不需要登录；BFF 对白名单来源开放 CORS）
    async catalog() {
      const base = engine();
      let r;
      try { r = await fetch(base + '/data/pattern_catalog.json', { credentials: 'omit', mode: 'cors', cache: 'no-cache' }); }
      catch (e) { throw netError(e); }
      if (!r.ok) throw new ApiError(r.status, 'http_' + r.status, `形态图鉴读取失败（HTTP ${r.status}）`, r.headers.get('X-Request-Id'));
      return r.json();
    },
    get: (p, o) => request('GET', p, o),
    post: (p, body, o) => request('POST', p, { ...(o || {}), body }),
    put: (p, body, o) => request('PUT', p, { ...(o || {}), body }),
    patch: (p, body, o) => request('PATCH', p, { ...(o || {}), body }),
    del: (p, o) => request('DELETE', p, o),
    opId() {
      const a = new Uint8Array(12); crypto.getRandomValues(a);
      return 'v' + Array.from(a, (b) => b.toString(16).padStart(2, '0')).join('');
    },
  };
  window.API = API;
})();
