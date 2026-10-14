'use strict';
/**
 * BFF 测试：鉴权、CSRF、限流、超时 / 错误映射、路由白名单、TLS 配置、schema 不兼容拒绝、安全头、版本身份。
 * 使用本地假上游（http.Server），不依赖 Python。
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const { loadConfig, ConfigError } = require('../src/config');
const { createApp } = require('../src/app');
const sec = require('../src/security');

const PW = 'correct-horse-battery';
const HASH = sec.hashPassword(PW, { N: 1024 });
const TOKEN = 'p'.repeat(40);

function upstreamServer(handler) {
  return new Promise((resolve) => {
    const calls = [];
    const s = http.createServer((req, res) => {
      let body = '';
      req.on('data', (d) => { body += d; });
      req.on('end', () => { calls.push({ method: req.method, url: req.url, headers: req.headers, body }); handler(req, res, body); });
    });
    s.listen(0, '127.0.0.1', () => resolve({ s, calls, url: `http://127.0.0.1:${s.address().port}` }));
  });
}

const json = (res, status, obj, headers = {}) => { res.writeHead(status, { 'content-type': 'application/json', ...headers }); res.end(JSON.stringify(obj)); };

function defaultHandler(req, res, body) {
  if (req.url === '/readyz') return json(res, 200, { status: 'ready' });
  if (req.url === '/api/v1/version') return json(res, 200, { engine: { api_schema_version: '1.0', analysis_schema_version: '3.0', verified: false } });
  if (req.url === '/api/v1/analyses') {
    const b = JSON.parse(body || '{}');
    if (b.code === '300999') return json(res, 503, { requestId: 'x', error: { code: 'market_data_unavailable', message: '行情源不可用', details: {} } });
    if (b.code === '600888') { setTimeout(() => json(res, 200, { analysis: {} }), 1500); return undefined; }
    return json(res, 200, { requestId: req.headers['x-request-id'], analysis: { code: b.code } });
  }
  if (req.url === '/api/v1/watchlist' && req.method === 'PATCH') return json(res, 200, { version: 2 }, { etag: 'W/"v2"' });
  if (req.url.startsWith('/api/v1/watchlist')) return json(res, 200, { version: 1, watchlist: [] }, { etag: 'W/"v1"' });
  return json(res, 200, { ok: true, url: req.url });
}

async function setup({ handler = defaultHandler, env = {}, fetchImpl } = {}) {
  const up = await upstreamServer(handler);
  const config = loadConfig({ NODE_ENV: 'development', DEPLOY_ENV: 'development', PYTHON_API_BASE_URL: up.url,
    PYTHON_API_TOKEN: TOKEN, SESSION_SECRET: 's'.repeat(40), LOGIN_USERNAME: 'owner', LOGIN_PASSWORD_HASH: HASH,
    UPSTREAM_ANALYSIS_TIMEOUT_MS: '1000', RATE_SCAN_CREATE_PER_MIN: '2', RATE_LOGIN_PER_15MIN: '5', ...env });
  const logs = [];
  const app = createApp(config, { fetchImpl, logger: { log: (l) => logs.push(l) } });
  const srv = await new Promise((r) => { const s = app.listen(0, '127.0.0.1', () => r(s)); });
  const base = `http://127.0.0.1:${srv.address().port}`;
  const close = () => { srv.close(); up.s.close(); };
  return { base, up, close, logs, config };
}

async function login(base) {
  const r = await fetch(`${base}/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: 'owner', password: PW }) });
  assert.equal(r.status, 200);
  const cookies = r.headers.getSetCookie().map((c) => c.split(';')[0]).join('; ');
  const { csrf } = await r.json();
  return { cookies, csrf };
}

test('config: TLS / secrets fail closed', () => {
  const base = { PYTHON_API_TOKEN: TOKEN, SESSION_SECRET: 's'.repeat(40), LOGIN_USERNAME: 'u', LOGIN_PASSWORD_HASH: HASH };
  assert.throws(() => loadConfig({ ...base, PYTHON_API_BASE_URL: 'http://py.example.com' }), ConfigError);         // 公网 http 拒绝
  assert.throws(() => loadConfig({ ...base, DEPLOY_ENV: 'staging', PYTHON_API_BASE_URL: 'http://localhost:8000' }), ConfigError);
  assert.ok(loadConfig({ ...base, PYTHON_API_BASE_URL: 'https://py.example.com' }));
  assert.ok(loadConfig({ ...base, PYTHON_API_BASE_URL: 'http://analysis:8000', PYTHON_API_PRIVATE_NETWORK: 'true' }));
  assert.throws(() => loadConfig({ ...base, PYTHON_API_BASE_URL: 'https://x.example.com', NODE_TLS_REJECT_UNAUTHORIZED: '0' }), /TLS/);
  assert.throws(() => loadConfig({ ...base, PYTHON_API_BASE_URL: 'https://x.example.com', PYTHON_API_TOKEN: 'short' }), /PYTHON_API_TOKEN/);
  assert.throws(() => loadConfig({ ...base, PYTHON_API_BASE_URL: 'https://x.example.com', LOGIN_PASSWORD_HASH: 'plain' }), /LOGIN_PASSWORD_HASH/);
  assert.throws(() => loadConfig({ ...base, PYTHON_API_BASE_URL: 'https://x.example.com', COOKIE_SECURE: 'false' }), /Secure/);
  assert.equal(loadConfig({ ...base, PYTHON_API_BASE_URL: 'https://x.example.com' }).cookieSecure, true);
});

test('password hashing and session tokens', () => {
  assert.ok(sec.verifyPassword(PW, HASH));
  assert.ok(!sec.verifyPassword('wrong', HASH));
  const { token, payload } = sec.issueSession('k'.repeat(40), 'owner', 60);
  assert.equal(sec.readSession('k'.repeat(40), token).u, 'owner');
  assert.equal(sec.readSession('x'.repeat(40), token), null);                          // 签名错误
  assert.equal(sec.readSession('k'.repeat(40), token, (payload.exp + 1) * 1000), null);  // 过期
  const [b, s] = token.split('.');
  const forged = Buffer.from(JSON.stringify({ ...payload, u: 'admin' })).toString('base64url');
  assert.equal(sec.readSession('k'.repeat(40), `${forged}.${s}`), null);
  assert.ok(b);
});

test('auth, csrf, proxy, request id, headers', async () => {
  const t = await setup();
  try {
    let r = await fetch(`${t.base}/api/v1/market`);
    assert.equal(r.status, 401);
    assert.equal((await r.json()).error.code, 'unauthenticated');
    r = await fetch(`${t.base}/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: 'owner', password: 'nope' }) });
    assert.equal(r.status, 401);
    const { cookies, csrf } = await login(t.base);
    r = await fetch(`${t.base}/api/v1/market`, { headers: { cookie: cookies, 'x-request-id': 'abcd-1234-efgh' } });
    assert.equal(r.status, 200);
    assert.equal(r.headers.get('x-request-id'), 'abcd-1234-efgh');
    assert.match(r.headers.get('content-security-policy'), /script-src 'self'/);
    assert.equal(r.headers.get('x-frame-options'), 'DENY');
    const call = t.up.calls.find((c) => c.url === '/api/v1/market');
    assert.equal(call.headers.authorization, `Bearer ${TOKEN}`);                 // 凭证只在服务端
    assert.equal(call.headers['x-user-id'], 'owner');
    assert.equal(call.headers['x-request-id'], 'abcd-1234-efgh');
    // 写操作缺 CSRF → 403
    r = await fetch(`${t.base}/api/v1/analyses`, { method: 'POST', headers: { cookie: cookies, 'content-type': 'application/json' }, body: JSON.stringify({ code: '002436' }) });
    assert.equal(r.status, 403);
    r = await fetch(`${t.base}/api/v1/analyses`, { method: 'POST', headers: { cookie: cookies, 'content-type': 'application/json', 'x-csrf-token': 'bogus' }, body: JSON.stringify({ code: '002436' }) });
    assert.equal(r.status, 403);
    // 跨站 Origin → 403
    r = await fetch(`${t.base}/api/v1/analyses`, { method: 'POST', headers: { cookie: cookies, 'content-type': 'application/json', 'x-csrf-token': csrf, origin: 'https://evil.example' }, body: JSON.stringify({ code: '002436' }) });
    assert.equal(r.status, 403);
    r = await fetch(`${t.base}/api/v1/analyses`, { method: 'POST', headers: { cookie: cookies, 'content-type': 'application/json', 'x-csrf-token': csrf }, body: JSON.stringify({ code: '002436' }) });
    assert.equal(r.status, 200);
    assert.equal((await r.json()).analysis.code, '002436');
    // 输入检查（BFF 层）
    r = await fetch(`${t.base}/api/v1/analyses`, { method: 'POST', headers: { cookie: cookies, 'content-type': 'application/json', 'x-csrf-token': csrf }, body: JSON.stringify({ code: '12' }) });
    assert.equal(r.status, 422);
    r = await fetch(`${t.base}/api/v1/scans/not-a-job`, { headers: { cookie: cookies } });
    assert.equal(r.status, 422);
    r = await fetch(`${t.base}/api/v1/unknown`, { headers: { cookie: cookies } });
    assert.equal(r.status, 404);
    r = await fetch(`${t.base}/api/v1/analyses`, { headers: { cookie: cookies } });                 // 方法不在白名单
    assert.equal(r.status, 404);
    // ETag / If-Match 透传
    r = await fetch(`${t.base}/api/v1/watchlist`, { method: 'PATCH', headers: { cookie: cookies, 'content-type': 'application/json', 'x-csrf-token': csrf, 'if-match': 'W/"v1"' }, body: JSON.stringify({ ops: [] }) });
    assert.equal(r.headers.get('etag'), 'W/"v2"');
    assert.equal(t.up.calls.find((c) => c.method === 'PATCH').headers['if-match'], 'W/"v1"');
    // 查询参数白名单
    await fetch(`${t.base}/api/v1/sectors/dynamic/886032?at=2026-10-10T00:00:00Z&evil=1`, { method: 'DELETE', headers: { cookie: cookies, 'x-csrf-token': csrf } });
    assert.equal(t.up.calls.find((c) => c.method === 'DELETE').url, '/api/v1/sectors/dynamic/886032?at=2026-10-10T00%3A00%3A00Z');
    // 日志不含凭证
    assert.ok(!t.logs.join('\n').includes(TOKEN));
  } finally { t.close(); }
});

test('upstream errors map to 502/503/504 with structured body', async () => {
  const t = await setup();
  try {
    const { cookies, csrf } = await login(t.base);
    const post = (code) => fetch(`${t.base}/api/v1/analyses`, { method: 'POST', headers: { cookie: cookies, 'content-type': 'application/json', 'x-csrf-token': csrf }, body: JSON.stringify({ code }) });
    let r = await post('300999');
    assert.equal(r.status, 503);
    assert.equal((await r.json()).error.code, 'market_data_unavailable');
    r = await post('600888');                                                      // 上游慢于超时
    assert.equal(r.status, 504);
    assert.equal((await r.json()).error.code, 'upstream_timeout');
  } finally { t.close(); }
  const dead = await setup();
  dead.up.s.close();
  try {
    const { cookies } = await login(dead.base);
    const r = await fetch(`${dead.base}/api/v1/market`, { headers: { cookie: cookies } });
    assert.equal(r.status, 503);                                                    // 兼容性检查先失败 → 拒绝
    const ready = await fetch(`${dead.base}/readyz`);
    assert.equal(ready.status, 503);
  } finally { dead.close(); }
});

test('upstream auth failure does not leak and schema mismatch is refused', async () => {
  const t = await setup({ handler: (req, res) => {
    if (req.url === '/api/v1/version') return json(res, 200, { engine: { api_schema_version: '0.9', analysis_schema_version: '3.0' } });
    return json(res, 401, { error: { code: 'unauthorized' } });
  } });
  try {
    const { cookies } = await login(t.base);
    const r = await fetch(`${t.base}/api/v1/market`, { headers: { cookie: cookies } });
    assert.equal(r.status, 503);
    const b = await r.json();
    assert.equal(b.error.code, 'incompatible_engine');
    const v = await (await fetch(`${t.base}/api/v1/version`, { headers: { cookie: cookies } })).json();
    assert.equal(v.compatibility.ok, false);
    assert.equal(v.frontend.version, 'Version Unknown');                            // FIR-28：模板元数据 → Unknown
  } finally { t.close(); }
  const t2 = await setup({ handler: (req, res) => {
    if (req.url === '/api/v1/version') return json(res, 200, { engine: { api_schema_version: '1.0', analysis_schema_version: '3.0' } });
    return json(res, 401, { error: { code: 'unauthorized', message: 'token xyz' } });
  } });
  try {
    const { cookies } = await login(t2.base);
    const r = await fetch(`${t2.base}/api/v1/market`, { headers: { cookie: cookies } });
    assert.equal(r.status, 502);
    const b = await r.json();
    assert.equal(b.error.code, 'upstream_auth_failed');
    assert.ok(!JSON.stringify(b).includes('xyz'));
  } finally { t2.close(); }
});

test('rate limits: login and scan creation', async () => {
  const t = await setup();
  try {
    for (let i = 0; i < 5; i += 1) {
      await fetch(`${t.base}/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: 'x', password: 'y' }) });
    }
    const r = await fetch(`${t.base}/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: 'owner', password: PW }) });
    assert.equal(r.status, 429);
    assert.ok(Number(r.headers.get('retry-after')) > 0);
  } finally { t.close(); }
  const t2 = await setup();
  try {
    const { cookies, csrf } = await login(t2.base);
    const mk = () => fetch(`${t2.base}/api/v1/scans`, { method: 'POST', headers: { cookie: cookies, 'content-type': 'application/json', 'x-csrf-token': csrf }, body: JSON.stringify({ scope: { type: 'watchlist' } }) });
    assert.equal((await mk()).status, 200);
    assert.equal((await mk()).status, 200);
    assert.equal((await mk()).status, 429);
  } finally { t2.close(); }
});

test('static web served with SPA fallback and no secrets', async () => {
  const t = await setup();
  try {
    let r = await fetch(`${t.base}/`);
    assert.equal(r.status, 200);
    const html = await r.text();
    assert.match(html, /<script src="js\/app.js"/);
    assert.ok(!/<script>/.test(html) && !/style="/.test(html));                       // CSP：无内联脚本 / 样式
    r = await fetch(`${t.base}/some/deep/link`);
    assert.equal(r.status, 200);
    r = await fetch(`${t.base}/js/api.js`);
    const js = await r.text();
    assert.ok(!js.includes(TOKEN) && !js.includes('PYTHON_API'));
    r = await fetch(`${t.base}/api/nope`);
    assert.equal(r.status, 404);
  } finally { t.close(); }
});
