/**
 * FIR-47 整改④：真实 Nest /api/analyze/:code 端到端（固定行情，不访问网络）。
 *
 * 与 test_us_acceptance.js 的区别：后者用模拟 fetch 直接喂 doc；本测试启动【真实构建的 Nest 服务】，
 * 通过 KLINE_FIXTURE_DIR 测试接缝给 Python 执行器注入冻结行情（生产不设此 env），
 * 走真实 HTTP /api/analyze/AAPL|NVDA → 真实 Python subprocess → Canonical Core，
 * 再核对：① 真实 API 信号字段 == 冻结 Core 信号；② 页面 doAnalyze 渲染 == 真实 API 信号。
 *
 * 运行：node tests/v6/test_us_nest_e2e.js（需先 nest build；CI 在 js-syntax-check job 内构建后运行）
 */
'use strict';
const assert = require('assert');
const fs = require('fs');
const http = require('http');
const net = require('net');
const os = require('os');
const path = require('path');
const { execFileSync, spawn } = require('child_process');
const { loadPage, ROOT } = require('./v4_page_harness');

const APP = path.join(ROOT, 'v6', 'app-builder');
const PY = process.env.PYTHON || 'python3';
const FIXTURES = path.join(ROOT, 'tests', 'v6', 'fixtures');
const EXPORTS = ['doAnalyze', 'buildMatchesFromDoc', 'pickDominant', 'PATTERNS', 'BEAR_PATTERNS'];

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); passed++; console.log('  ✓ ' + name); }
  catch (e) { failed++; console.log('  ✗ ' + name + '\n      ' + (e && e.message ? e.message.split('\n').join('\n      ') : e)); }
}
const flush = () => new Promise(r => setImmediate(r));
async function settle(n = 8) { for (let i = 0; i < n; i++) await flush(); }
function freePort() { return new Promise(res => { const s = net.createServer().listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); }); }); }
function get(port, p, csrf) {
  return new Promise((resolve, reject) => {
    const headers = {};
    if (csrf) { headers['Cookie'] = `suda-csrf-token=${csrf}`; headers['x-suda-csrf-token'] = csrf; }
    http.get({ host: '127.0.0.1', port, path: p, headers }, res => {
      let body = ''; res.on('data', d => body += d); res.on('end', () => resolve({ status: res.statusCode, body, headers: res.headers }));
    }).on('error', reject);
  });
}
function csrfFrom(setCookie) {
  const arr = setCookie || [];
  for (const c of arr) { const m = /suda-csrf-token=([^;]+)/.exec(c); if (m) return m[1]; }
  return null;
}
function nameOf(p, id) { const all = [...(p.x.PATTERNS || []), ...(p.x.BEAR_PATTERNS || [])]; const h = all.find(x => x.id === id); return h ? h.name : id; }

(async () => {
  // 冻结 Core 基准（与 test_us_acceptance 同一份 fixture + 固定时间）
  const DOCS = JSON.parse(execFileSync(PY, [path.join(ROOT, 'tests', 'v6', 'us_acceptance_make_docs.py')], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 }));

  console.log('构建真实 Nest 服务（nest build）');
  execFileSync(path.join(APP, 'node_modules', '.bin', 'nest'), ['build'], { cwd: APP, env: { ...process.env, NODE_ENV: 'production' }, stdio: 'pipe' });

  const port = await freePort();
  const tmpdir = fs.mkdtempSync(path.join(os.tmpdir(), 'us-e2e-'));
  let log = '';
  const proc = spawn(process.execPath, [path.join(ROOT, 'tests', 'v6', 'us_nest_boot.js')], {
    cwd: APP,
    env: {
      PATH: process.env.PATH, HOME: process.env.HOME, TMPDIR: tmpdir,
      SERVER_HOST: '127.0.0.1', SERVER_PORT: String(port),
      KLINE_FIXTURE_DIR: FIXTURES,                       // 测试接缝：注入冻结行情（生产不设）
      WATCHLIST_STORE: 'memory',
    },
  });
  proc.stdout.on('data', d => { log += d; });
  proc.stderr.on('data', d => { log += d; });
  const t0 = Date.now();
  let ready = false;
  while (!ready) {
    if (proc.exitCode !== null) { console.error('服务启动失败:\n' + log); process.exit(1); }
    if (Date.now() - t0 > 90000) { console.error('服务启动超时:\n' + log); proc.kill('SIGKILL'); process.exit(1); }
    try { const r = await get(port, '/api/health'); if (r.status === 200) ready = true; } catch (e) { /* 尚未监听 */ }
    if (!ready) await new Promise(r => setTimeout(r, 200));
  }

  try {
    console.log('一、真实 /api/health：real_core');
    await test('health core_analyzer=real_core', async () => {
      const r = await get(port, '/api/health');
      assert.strictEqual(r.status, 200);
      const h = JSON.parse(r.body);
      assert.strictEqual(h.core_analyzer, 'real_core');
    });

    console.log('二、真实 /api/analyze/:code 信号字段 == 冻结 Core');
    const realDocs = {};
    for (const ticker of ['AAPL', 'NVDA']) {
      await test(`${ticker}：真实 Nest API 经 Python subprocess 产出，信号与冻结 Core 逐字段一致`, async () => {
        const r = await get(port, `/api/analyze/${ticker}`);
        assert.strictEqual(r.status, 200, `HTTP ${r.status}: ${r.body.slice(0, 200)}`);
        const doc = JSON.parse(r.body);
        realDocs[ticker] = doc;
        const core = DOCS[ticker].core;
        assert.strictEqual(doc.engine.analyzer, 'real_core');
        assert.strictEqual(doc.input.data_hash, core.input.data_hash, 'data_hash');
        assert.strictEqual(doc.input.bar_count, 250);
        assert.strictEqual(doc.core.result_sha256, core.core.result_sha256, 'result_sha256');
        assert.strictEqual(doc.signals.signals_sha256, core.signals.signals_sha256, 'signals_sha256');
        assert.deepStrictEqual(doc.signals.dominant, core.signals.dominant, 'dominant');
        for (const pid of core.engine.pattern_order) {
          const c = core.signals.latest[pid] || {}, a = doc.signals.latest[pid] || {};
          assert.strictEqual(a.score, c.score, `${pid}.score`);
          assert.strictEqual(a.stage, c.stage, `${pid}.stage`);
          assert.strictEqual(a.confirmed, c.confirmed, `${pid}.confirmed`);
        }
      });
    }

    console.log('三、页面 doAnalyze 渲染 == 真实 Nest API 信号');
    for (const ticker of ['AAPL', 'NVDA']) {
      await test(`${ticker}：页面主导形态/分数/确认来自真实 API`, async () => {
        const doc = realDocs[ticker];
        const p = loadPage({ exports: EXPORTS, canvasIds: ['chartFull', 'chart60'], fetch: async () => ({ ok: true, status: 200, json: async () => doc }) });
        p.sandbox.fetchSinaKlineJSONP = async () => null;
        p.sandbox.document.getElementById('codeInput').value = ticker;
        await p.x.doAnalyze(); await settle();
        const html = p.sandbox.document.getElementById('resultArea').innerHTML;
        const dom = doc.signals.dominant.overall;
        assert.ok(html.includes(nameOf(p, dom.pattern_id)), '页面应展示真实 API 主导形态名');
        assert.ok(html.includes(`${doc.signals.latest[dom.pattern_id].score}/100`), '页面应展示真实 API 信号强度');
        assert.ok(html.includes(dom.confirmed ? '已确认' : '未确认'), '确认状态与真实 API 一致');
        assert.ok(!html.includes('相对强度（RS）'), '美股个股页隐藏相对强度');
      });
    }
  } finally {
    proc.kill('SIGKILL');
  }

  console.log(`\n结果: ${passed} 通过, ${failed} 失败`);
  process.exit(failed ? 1 : 0);
})();