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
  catch (e) { failed++; console.log('  ✗ ' + name + '\n      ' + (e && e.stack ? e.stack.split('\n').slice(0, 6).join('\n      ') : e)); }
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
function req(port, method, p, body) {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? null : JSON.stringify(body);
    const headers = { 'Accept': 'application/json' };
    if (data) headers['Content-Type'] = 'application/json';
    const r = http.request({ host: '127.0.0.1', port, path: p, method, headers }, res => {
      let b = ''; res.on('data', d => b += d); res.on('end', () => resolve({ status: res.statusCode, body: b, headers: res.headers }));
    });
    r.on('error', reject);
    if (data) r.write(data);
    r.end();
  });
}
async function pollJob(port, id, ms = 60000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const r = await get(port, `/api/scan/jobs/${id}`);
    const j = JSON.parse(r.body);
    if (j.status === 'done' || j.status === 'cancelled') return j;
    await new Promise(r => setTimeout(r, 150));
  }
  throw new Error(`扫描任务 ${id} 超时未结束`);
}
const json = (r) => JSON.parse(r.body);
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

    console.log('四、FIR-48：已登记清单；格式错误 / 未登记分开报错（INVALID_CODE / UNSUPPORTED_CODE）');
    await test('GET /api/supported-codes：已登记美股清单与规则随同源模块返回', async () => {
      const r = await get(port, '/api/supported-codes');
      assert.strictEqual(r.status, 200, r.body);
      const j = json(r);
      for (const t of ['AAPL', 'NVDA', 'BRK.B']) assert.ok(j.us.includes(t), `清单缺 ${t}`);
      assert.ok(j.rules && j.rules.cn && j.rules.us);
    });
    await test('ZZZZ：格式合法但未登记 → 400 UNSUPPORTED_CODE；60051：格式错误 → 400 INVALID_CODE', async () => {
      const u = await get(port, '/api/analyze/ZZZZ');
      assert.strictEqual(u.status, 400, u.body);
      assert.strictEqual(json(u).error, 'UNSUPPORTED_CODE');
      const b = await get(port, '/api/analyze/60051');
      assert.strictEqual(b.status, 400, b.body);
      assert.strictEqual(json(b).error, 'INVALID_CODE');
    });

    console.log('五、FIR-48：自选池双市场混存；归一化去重；导入分类；墓碑不复活');
    await test('添加 AAPL / BRK.B / 600519；大小写空格变体归一为一条', async () => {
      let r = await req(port, 'POST', '/api/watchlist/aapl');
      assert.ok([200, 201].includes(r.status), r.body);
      assert.strictEqual(json(r).added, true);
      r = await req(port, 'POST', '/api/watchlist/AAPL');
      assert.strictEqual(json(r).added, false, '重复加入幂等');
      await req(port, 'POST', '/api/watchlist/BRK.B');
      await req(port, 'POST', '/api/watchlist/600519');
      const v = json(await get(port, '/api/watchlist'));
      assert.deepStrictEqual(v.items, ['AAPL', 'BRK.B', '600519']);
    });
    await test('导入：NVDA 入库；ZZZZ 列入 skippedUnsupported，60051 列入 skipped；墓碑不复活', async () => {
      const r = await req(port, 'POST', '/api/watchlist/import', { codes: ['nvda', 'ZZZZ', '60051'], tombstones: [] });
      assert.ok([200, 201].includes(r.status), r.body);
      const v = json(r);
      assert.ok(v.items.includes('NVDA'));
      assert.deepStrictEqual(v.skipped, ['ZZZZ', '60051']);
      assert.deepStrictEqual(v.skippedUnsupported, ['ZZZZ']);
      // 墓碑：删除 AAPL 后旧设备导入 'aapl' 不得复活
      await req(port, 'DELETE', '/api/watchlist/AAPL');
      const r2 = await req(port, 'POST', '/api/watchlist/import', { codes: ['aapl'], tombstones: [] });
      const v2 = json(r2);
      assert.ok(!v2.items.includes('AAPL'), '已删除记录不应被旧设备导入复活');
    });

    console.log('六、FIR-48：自定义板块 A 股 + 美股混合；错误成分分开拒绝');
    await test('混合板块保存 600519 / AAPL / BRK.B；未登记与格式错误被 400 拒绝', async () => {
      const r = await req(port, 'PUT', '/api/custom-sectors/%E6%B7%B7%E5%90%88', { codes: ['600519', 'AAPL', 'BRK.B'] });
      assert.ok([200, 201].includes(r.status), r.body);
      const view = json(await get(port, '/api/custom-sectors'));
      const sec = view.items.find(i => i.name === '混合');
      assert.ok(sec, '混合板块应在清单中');
      assert.deepStrictEqual(sec.codes, ['600519', 'AAPL', 'BRK.B']);
      const u = await req(port, 'PUT', '/api/custom-sectors/%E6%9C%AA%E7%99%BB%E8%AE%B0', { codes: ['ZZZZ'] });
      assert.strictEqual(u.status, 400, u.body);
      assert.strictEqual(json(u).error, 'UNSUPPORTED_CODE');
      const b = await req(port, 'PUT', '/api/custom-sectors/%E6%A0%BC%E5%BC%8F%E9%94%99%E8%AF%AF', { codes: ['60051'] });
      assert.strictEqual(b.status, 400, b.body);
      assert.strictEqual(json(b).error, 'INVALID_CODE');
    });

    console.log('七、FIR-48：扫描按市场分流；单只失败其余继续；扫描结果可回加自选');
    await test('混合扫描 600519 + BRK.B 全部成功；失败清单为空', async () => {
      const r = await req(port, 'POST', '/api/scan/jobs', { codes: ['600519', 'BRK.B'] });
      assert.strictEqual(r.status, 201, r.body);
      const job = await pollJob(port, json(r).job_id);
      assert.strictEqual(job.status, 'done');
      assert.strictEqual(job.failed_count, 0, JSON.stringify(job.failed));
      const codes = job.items.map(i => i.code).sort();
      assert.deepStrictEqual(codes, ['600519', 'BRK.B']);
    });
    await test('单只取数失败只标记该行，其余继续；不降级本地算法', async () => {
      const r = await req(port, 'POST', '/api/scan/jobs', { codes: ['600519', '600999'] });
      const job = await pollJob(port, json(r).job_id);
      assert.strictEqual(job.status, 'done');
      assert.deepStrictEqual(job.items.map(i => i.code), ['600519']);
      assert.strictEqual(job.failed_count, 1);
      assert.strictEqual(job.failed[0].code, '600999');
      assert.strictEqual(job.items[0].engine, 'real_core');
    });
    await test('扫描结果回加自选：NVDA 提交入库并出现在清单', async () => {
      const r = await req(port, 'POST', '/api/watchlist/NVDA');
      assert.ok([200, 201].includes(r.status), r.body);
      const v = json(await get(port, '/api/watchlist'));
      assert.ok(v.items.includes('NVDA'));
    });

    console.log('八、FIR-48：BRK.B 详情与批量行共同字段一致（日期 / 价格 / 主导形态 / 分数 / 确认）');
    await test('BRK.B 个股 API 与扫描批量行字段对齐', async () => {
      const d = json(await get(port, '/api/analyze/BRK.B'));
      const r = await req(port, 'POST', '/api/scan/jobs', { codes: ['BRK.B'] });
      const job = await pollJob(port, json(r).job_id);
      const item = job.items.find(i => i.code === 'BRK.B');
      assert.ok(item, '批量行应包含 BRK.B');
      assert.strictEqual(item.as_of, d.summary.as_of, '分析日期一致');
      assert.strictEqual(Number(item.price), Number(d.summary.close), '价格一致');
      const overall = d.signals.dominant.overall;
      if (overall && overall.pattern_id) {
        const top = item.bull_top && item.bull_top.pid === overall.pattern_id ? item.bull_top : item.bear_top;
        assert.ok(top, '批量行主导形态应与个股 API 一致');
        assert.strictEqual(top.score, d.signals.latest[overall.pattern_id].score, '分数一致');
        assert.strictEqual(top.confirmed, overall.confirmed, '确认状态一致');
      }
      assert.ok(!('relative_strength' in item) && !('market_env' in item), '美股批量行不带上证大盘 / RS 字段');
    });

    console.log('九、FIR-48：自选清单 >200 只「分析全部」分批跑完整清单，总进度与失败数准确');
    await test('201 只清单分两批（每批 ≤200）跑完；1 只成功、200 只失败逐项标记', async () => {
      const extra = [];
      for (let i = 0; i < 198; i++) extra.push(`600${String(i).padStart(3, '0')}`);
      const imp = await req(port, 'POST', '/api/watchlist/import', { codes: extra, tombstones: [] });
      assert.ok([200, 201].includes(imp.status), imp.body);
      const before = json(await get(port, '/api/watchlist')).items;
      assert.strictEqual(before.length, 201, `清单应为 201 只（3 只有 fixture + 198 只缺 fixture），实际 ${before.length}`);
      const r = await req(port, 'POST', '/api/watchlist/analyze');
      assert.ok([200, 201].includes(r.status), r.body);
      const res = json(r);
      assert.strictEqual(res.failedCount, 198, '缺 fixture 的 198 只应逐项计入失败');
      assert.strictEqual(res.count, 3, '成功行 = 有 fixture 的 3 只');
      for (const it of res.items) assert.strictEqual(it.engine, 'real_core');
      assert.strictEqual(res.failed.length, 198);
    });

    console.log('十、FIR-48：页面混合清单渲染（harness，无网络）');
    await test('页面自选股标签同时渲染 A 股与美股；无大盘悬挂态', async () => {
      const stub = async (url) => {
        if (/\/supported-codes$/.test(String(url))) return { ok: true, status: 200, json: async () => ({ us: ['AAPL', 'NVDA', 'BRK.B'] }) };
        if (/\/watchlist$/.test(String(url))) return { ok: true, status: 200, json: async () => ({ items: ['600519', 'AAPL', 'BRK.B'], tombstones: [], count: 3, rev: 1, storage: 'memory' }) };
        return { ok: false, status: 404, json: async () => ({ message: 'stub' }) };
      };
      const p = loadPage({ fetch: stub, exports: ['renderWatchlistTags', 'getWatchlist'], immediateTimers: true });
      await settle();
      const tags = p.sandbox.document.getElementById('watchlistTags').innerHTML || '';
      for (const c of ['600519', 'AAPL', 'BRK.B']) assert.ok(tags.includes(c), `标签缺 ${c}`);
      assert.ok(!tags.includes('大盘数据获取中'), '混合清单不应有大盘悬挂加载态');
    });
  } finally {
    proc.kill('SIGKILL');
  }

  console.log(`\n结果: ${passed} 通过, ${failed} 失败`);
  process.exit(failed ? 1 : 0);
})();