'use strict';
/**
 * V6 BFF（FIR-31 §4）：只做 展示资源 / 鉴权 / CSRF / 限流 / 输入检查 / 超时 / 转发。
 * 不含任何形态、指标、评分或大盘判断 —— 所有结果来自 Python 分析服务（单一 core）。
 */
const crypto = require('crypto');
const path = require('path');
const express = require('express');
const sec = require('./security');
const ReleaseIdentity = require('../../../release_identity.js');

const ID_RE = /^[A-Za-z0-9-]{8,64}$/;
const CODE_RE = /^\d{6}$/;
const JOB_RE = /^[0-9a-f]{32}$/;
const SECTOR_NAME_RE = /^[\w一-鿿·（）()\-+ ]{1,20}$/u;
const QUERY_KEYS = new Set(['asOf', 'at', 'matchedOnly']);

// 允许转发的路由（方法 + 路径模式 + 超时类别 + 限流类别）
const ROUTES = [
  ['GET', /^\/api\/v1\/market$/, 'default'],
  ['GET', /^\/api\/v1\/calibration$/, 'default'],
  ['GET', /^\/api\/v1\/meta\/patterns$/, 'default'],
  ['POST', /^\/api\/v1\/analyses$/, 'analysis', 'analysis'],
  ['GET', /^\/api\/v1\/watchlist$/, 'default'],
  ['PATCH', /^\/api\/v1\/watchlist$/, 'default'],
  ['GET', /^\/api\/v1\/watchlist\/export$/, 'default'],
  ['GET', /^\/api\/v1\/sectors$/, 'default'],
  ['PUT', /^\/api\/v1\/sectors\/custom\/([^/]+)$/, 'default', null, 'sectorName'],
  ['DELETE', /^\/api\/v1\/sectors\/custom\/([^/]+)$/, 'default', null, 'sectorName'],
  ['POST', /^\/api\/v1\/sectors\/dynamic$/, 'analysis'],          // 会自动拉同花顺成分股
  ['DELETE', /^\/api\/v1\/sectors\/dynamic\/([^/]+)$/, 'default', null, 'code'],
  ['POST', /^\/api\/v1\/sectors\/([^/]+)\/verification$/, 'analysis', null, 'code'],
  ['POST', /^\/api\/v1\/scans$/, 'analysis', 'scanCreate'],      // 扫描前可能重拉板块成分股
  ['GET', /^\/api\/v1\/scans$/, 'default'],
  ['GET', /^\/api\/v1\/scans\/([^/]+)$/, 'default', null, 'job'],
  ['GET', /^\/api\/v1\/scans\/([^/]+)\/results$/, 'default', null, 'job'],
  ['POST', /^\/api\/v1\/scans\/([^/]+)\/cancel$/, 'default', null, 'job'],
];

function errorBody(requestId, status, code, message, details) {
  return { status, body: { requestId, error: { code, message, details: details || {} } } };
}

class Semaphore {
  constructor(n) { this.n = n; this.q = []; }
  async acquire() {
    if (this.n > 0) { this.n -= 1; return; }
    await new Promise((r) => this.q.push(r));
  }
  release() { const r = this.q.shift(); if (r) r(); else this.n += 1; }
}

function createApp(config, { fetchImpl = globalThis.fetch, logger = console } = {}) {
  const app = express();
  app.disable('x-powered-by');
  app.set('trust proxy', config.trustProxy);
  const sem = new Semaphore(config.upstreamConcurrency);
  const limiters = {
    api: new sec.RateLimiter(config.rate.apiPerMin, 60000),
    analysis: new sec.RateLimiter(config.rate.analysisPerMin, 60000),
    scanCreate: new sec.RateLimiter(config.rate.scanCreatePerMin, 60000),
    login: new sec.RateLimiter(config.rate.loginPer15Min, 15 * 60000),
  };
  let schemaCheck = { at: 0, ok: false, info: null, reason: 'not_checked' };

  const log = (obj) => logger.log(JSON.stringify({ ts: new Date().toISOString(), ...obj }));

  // ---------------------------------------------------------------- 通用中间件
  app.use((req, res, next) => {
    const incoming = req.get('x-request-id');
    req.requestId = incoming && ID_RE.test(incoming) ? incoming : crypto.randomUUID();
    res.setHeader('X-Request-Id', req.requestId);
    const t0 = process.hrtime.bigint();
    res.on('finish', () => {
      log({ event: 'request', requestId: req.requestId, method: req.method, path: req.path, status: res.statusCode,
        ms: Number(process.hrtime.bigint() - t0) / 1e6, user: req.session ? req.session.u : null });
    });
    next();
  });
  app.use(sec.securityHeaders(config.cookieSecure));

  const send = (res, { status, body }) => res.status(status).json(body);

  // 跨域（CORS）：只对白名单来源开放 /api、/auth/token 和形态图鉴数据 /data；不带 Cookie（Allow-Credentials 不设置）
  const corsOrigins = new Set(config.corsOrigins || []);
  app.use(['/api', '/auth/token', '/data'], (req, res, next) => {
    const origin = req.get('origin');
    if (origin && corsOrigins.has(origin)) {
      res.setHeader('Access-Control-Allow-Origin', origin);
      res.setHeader('Vary', 'Origin');
      res.setHeader('Access-Control-Expose-Headers', 'ETag, X-Request-Id, Retry-After');
      if (req.method === 'OPTIONS') {
        res.setHeader('Access-Control-Allow-Methods', 'GET, POST, PUT, PATCH, DELETE');
        res.setHeader('Access-Control-Allow-Headers', 'Authorization, Content-Type, If-Match, X-Request-Id');
        res.setHeader('Access-Control-Max-Age', '600');
        return res.status(204).end();
      }
    }
    return next();
  });

  function session(req) {
    const c = sec.parseCookies(req.headers.cookie);
    return sec.readSession(config.sessionSecret, c[sec.SESSION_COOKIE]);
  }

  function bearer(req) {
    const m = /^Bearer (.+)$/.exec(req.get('authorization') || '');
    return m ? sec.readSession(config.sessionSecret, m[1]) : undefined;
  }

  function rate(kind, key, req, res) {
    const r = limiters[kind].hit(key);
    if (!r.ok) {
      res.setHeader('Retry-After', String(r.retryAfterS));
      send(res, errorBody(req.requestId, 429, 'rate_limited', `请求过于频繁，请 ${r.retryAfterS} 秒后再试`));
      return false;
    }
    return true;
  }

  // ---------------------------------------------------------------- 上游调用
  async function upstream(method, pathAndQuery, { body, headers = {}, timeoutMs, requestId, userId } = {}) {
    await sem.acquire();
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const r = await fetchImpl(config.pythonBase + pathAndQuery, {
        method, signal: ctrl.signal, redirect: 'error',
        headers: { Authorization: `Bearer ${config.pythonToken}`, 'X-Request-Id': requestId,
          ...(userId ? { 'X-User-Id': userId } : {}), Accept: 'application/json',
          ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...headers },
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
      const text = await r.text();
      let json = null;
      try { json = text ? JSON.parse(text) : null; } catch (e) { json = null; }
      return { status: r.status, json, etag: r.headers.get('etag') };
    } catch (e) {
      if (e.name === 'AbortError') return { error: 'timeout' };
      return { error: 'unreachable', detail: String(e.cause ? e.cause.code || e.cause.message : e.message).slice(0, 120) };
    } finally {
      clearTimeout(timer);
      sem.release();
    }
  }

  function mapUpstream(req, r) {
    if (r.error === 'timeout') return errorBody(req.requestId, 504, 'upstream_timeout', '分析服务响应超时，请稍后重试');
    if (r.error) return errorBody(req.requestId, 502, 'upstream_unreachable', '无法连接分析服务', { reason: r.detail });
    if (r.status === 401 || r.status === 403) {
      return errorBody(req.requestId, 502, 'upstream_auth_failed', '分析服务拒绝了服务端凭证（请检查部署配置）');
    }
    if (!r.json || typeof r.json !== 'object') {
      return errorBody(req.requestId, 502, 'upstream_bad_response', '分析服务返回了无法解析的响应');
    }
    return { status: r.status, body: { ...r.json, requestId: req.requestId } };
  }

  async function ensureCompatible(req) {
    const now = Date.now();
    if (now - schemaCheck.at < (schemaCheck.ok ? 60000 : 5000)) return schemaCheck;
    const r = await upstream('GET', '/api/v1/version', { timeoutMs: config.timeouts.ready, requestId: req.requestId });
    if (r.error || r.status !== 200 || !r.json || !r.json.engine) {
      schemaCheck = { at: now, ok: false, info: null, reason: r.error || `status_${r.status}` };
    } else {
      const e = r.json.engine;
      const ok = e.api_schema_version === config.expectedSchemas.api && e.analysis_schema_version === config.expectedSchemas.analysis;
      schemaCheck = { at: now, ok, info: e,
        reason: ok ? null : `schema mismatch: api ${e.api_schema_version} / analysis ${e.analysis_schema_version}` };
    }
    return schemaCheck;
  }

  // ---------------------------------------------------------------- 健康
  app.get('/healthz', (req, res) => res.json({ status: 'ok', service: 'kline-bff' }));
  app.get('/readyz', async (req, res) => {
    const r = await upstream('GET', '/readyz', { timeoutMs: config.timeouts.ready, requestId: req.requestId });
    const compat = await ensureCompatible(req);
    const pyReady = !r.error && r.status === 200;
    const ready = pyReady && compat.ok;
    res.status(ready ? 200 : 503).json({ status: ready ? 'ready' : 'not_ready',
      checks: { python: pyReady ? 'ready' : (r.error || `status_${r.status}`), schema: compat.ok ? 'compatible' : compat.reason } });
  });

  // ---------------------------------------------------------------- 鉴权
  app.post('/auth/login', express.json({ limit: '4kb' }), (req, res) => {
    if (!rate('login', `ip:${req.ip}`, req, res)) return;
    const { username, password } = req.body || {};
    const okUser = typeof username === 'string' && sec.safeEqual(username, config.loginUsername);
    const okPass = typeof password === 'string' && password.length <= 256 && sec.verifyPassword(password, config.passwordHash);
    if (!okUser || !okPass) {
      return send(res, errorBody(req.requestId, 401, 'invalid_credentials', '用户名或密码错误'));
    }
    const { token, payload } = sec.issueSession(config.sessionSecret, config.userId, config.sessionTtlS);
    res.setHeader('Set-Cookie', [
      sec.cookie(sec.SESSION_COOKIE, token, { maxAge: config.sessionTtlS, secure: config.cookieSecure }),
      sec.cookie(sec.CSRF_COOKIE, payload.csrf, { maxAge: config.sessionTtlS, secure: config.cookieSecure, httpOnly: false }),
    ]);
    res.json({ requestId: req.requestId, authenticated: true, user: payload.u, csrf: payload.csrf, expiresAt: payload.exp });
  });

  // 令牌登录：给跨域网页用（如妙搭托管的 V5 界面）。令牌放在 Authorization 头里，浏览器不会自动带上，所以不需要 CSRF。
  app.post('/auth/token', express.json({ limit: '4kb' }), (req, res) => {
    if (!rate('login', `ip:${req.ip}`, req, res)) return;
    const { username, password } = req.body || {};
    const okUser = typeof username === 'string' && sec.safeEqual(username, config.loginUsername);
    const okPass = typeof password === 'string' && password.length <= 256 && sec.verifyPassword(password, config.passwordHash);
    if (!okUser || !okPass) {
      return send(res, errorBody(req.requestId, 401, 'invalid_credentials', '用户名或密码错误'));
    }
    const { token, payload } = sec.issueSession(config.sessionSecret, config.userId, config.sessionTtlS);
    res.setHeader('Cache-Control', 'no-store');
    res.json({ requestId: req.requestId, authenticated: true, user: payload.u, token, expiresAt: payload.exp });
  });

  app.post('/auth/logout', (req, res) => {
    res.setHeader('Set-Cookie', [
      sec.cookie(sec.SESSION_COOKIE, '', { maxAge: 0, secure: config.cookieSecure }),
      sec.cookie(sec.CSRF_COOKIE, '', { maxAge: 0, secure: config.cookieSecure, httpOnly: false }),
    ]);
    res.json({ requestId: req.requestId, authenticated: false });
  });

  app.get('/auth/session', (req, res) => {
    const s = session(req);
    res.json({ requestId: req.requestId, authenticated: !!s, user: s ? s.u : null, csrf: s ? s.csrf : null,
      expiresAt: s ? s.exp : null });
  });

  // ---------------------------------------------------------------- API
  const apiGuard = (req, res, next) => {
    const b = bearer(req);
    if (b === null) return send(res, errorBody(req.requestId, 401, 'unauthenticated', '登录已过期，请重新登录'));
    if (b) {   // 令牌访问：跨域来源必须在白名单里
      const origin = req.get('origin');
      if (origin && origin !== `${req.protocol}://${req.get('host')}` && !corsOrigins.has(origin)) {
        return send(res, errorBody(req.requestId, 403, 'cross_origin', '这个网页来源不在允许列表里（CORS_ALLOWED_ORIGINS）'));
      }
      req.session = b;
      if (!rate('api', `u:${b.u}`, req, res)) return;
      return next();
    }
    const s = session(req);
    if (!s) return send(res, errorBody(req.requestId, 401, 'unauthenticated', '请先登录'));
    req.session = s;
    if (!['GET', 'HEAD'].includes(req.method)) {
      const c = sec.parseCookies(req.headers.cookie)[sec.CSRF_COOKIE];
      const h = req.get('x-csrf-token');
      if (!h || !c || !sec.safeEqual(h, c) || !sec.safeEqual(h, s.csrf)) {
        return send(res, errorBody(req.requestId, 403, 'csrf_failed', '安全校验失败，请刷新页面后重试'));
      }
      const origin = req.get('origin');
      if (origin && origin !== `${req.protocol}://${req.get('host')}`) {
        return send(res, errorBody(req.requestId, 403, 'cross_origin', '拒绝跨站请求'));
      }
    }
    if (!rate('api', `u:${s.u}`, req, res)) return;
    next();
  };

  app.get('/api/v1/version', apiGuard, async (req, res) => {
    const frontend = ReleaseIdentity.makeDisplay(config.releaseInfo);
    const r = await upstream('GET', '/api/v1/version', { timeoutMs: config.timeouts.ready, requestId: req.requestId,
      userId: req.session.u });
    let engine = { verified: false, version: null, commit: null, reachable: false };
    if (!r.error && r.status === 200 && r.json && r.json.engine) engine = { ...r.json.engine, reachable: true };
    const compat = await ensureCompatible(req);
    res.json({ requestId: req.requestId, frontend: { ...frontend, component: 'web+bff' }, engine,
      compatibility: { ok: compat.ok, reason: compat.reason, expected: config.expectedSchemas } });
  });

  const bodyParser = express.json({ limit: '1mb', strict: true });
  app.all('/api/v1/*', apiGuard, bodyParser, async (req, res) => {
    const route = ROUTES.find(([m, re]) => m === req.method && re.test(req.path));
    if (!route) return send(res, errorBody(req.requestId, 404, 'not_found', '接口不存在'));
    const [, re, timeoutKind, rateKind, paramKind] = route;
    if (paramKind) {
      const raw = decodeURIComponent(req.path.match(re)[1]);
      const ok = paramKind === 'code' ? CODE_RE.test(raw) : paramKind === 'job' ? JOB_RE.test(raw)
        : SECTOR_NAME_RE.test(raw);
      if (!ok) return send(res, errorBody(req.requestId, 422, 'invalid_request', '路径参数不合法'));
    }
    if (req.method === 'POST' && req.path === '/api/v1/analyses') {
      const b = req.body || {};
      if (typeof b.code !== 'string' || !CODE_RE.test(b.code)) {
        return send(res, errorBody(req.requestId, 422, 'invalid_request', '股票代码必须是 6 位数字'));
      }
    }
    if (rateKind && !rate(rateKind, `u:${req.session.u}`, req, res)) return;
    const compat = await ensureCompatible(req);
    if (!compat.ok) {
      return send(res, errorBody(req.requestId, 503, 'incompatible_engine',
        '分析服务版本与网页不兼容或不可用，已拒绝请求（不会降级到其他算法）', { reason: compat.reason }));
    }
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(req.query)) if (QUERY_KEYS.has(k) && typeof v === 'string') qs.set(k, v);
    const q = qs.toString();
    const hasBody = ['POST', 'PUT', 'PATCH'].includes(req.method);
    const headers = {};
    if (req.get('if-match')) headers['If-Match'] = req.get('if-match');
    const r = await upstream(req.method, req.path + (q ? `?${q}` : ''), {
      body: hasBody ? (req.body || {}) : undefined, headers, requestId: req.requestId, userId: req.session.u,
      timeoutMs: timeoutKind === 'analysis' ? config.timeouts.analysis : config.timeouts.connectAndDefault });
    if (r.etag) res.setHeader('ETag', r.etag);
    res.setHeader('Cache-Control', 'no-store');
    send(res, mapUpstream(req, r));
  });

  app.use('/api', (req, res) => send(res, errorBody(req.requestId, 404, 'not_found', '接口不存在')));

  // ---------------------------------------------------------------- 静态网页
  // V5 界面（数据全部来自同一个 Python 引擎）：/v5/
  if (config.staticDirV5) {
    app.use('/v5', express.static(config.staticDirV5, { index: 'index.html', maxAge: 0, etag: true,
      setHeaders: (res, p) => { if (p.endsWith('.html')) res.setHeader('Cache-Control', 'no-cache'); } }));
  }
  app.use(express.static(config.staticDir, { index: 'index.html', maxAge: 0, etag: true,
    setHeaders: (res, p) => { if (p.endsWith('.html')) res.setHeader('Cache-Control', 'no-cache'); } }));
  app.get('*', (req, res) => res.sendFile(path.join(config.staticDir, 'index.html')));

  // JSON 解析错误等
  // eslint-disable-next-line no-unused-vars
  app.use((err, req, res, next) => {
    if (err && err.type === 'entity.too.large') return send(res, errorBody(req.requestId, 413, 'payload_too_large', '请求体过大'));
    if (err && err.type === 'entity.parse.failed') return send(res, errorBody(req.requestId, 400, 'bad_json', '请求体不是合法 JSON'));
    log({ event: 'error', requestId: req.requestId, error: String(err && err.stack || err).slice(0, 500) });
    send(res, errorBody(req.requestId, 500, 'internal_error', '服务内部错误'));
  });

  return app;
}

module.exports = { createApp, ROUTES };
