/**
 * FIR-41 第 1 项：扫描后台任务。
 * - 后端：编译部署的 scan-jobs.ts，用假 runner 验证并发上限、去重、上限、超时、失败清单、筛选排序、取消、过期清理。
 * - 前端：加载实际部署的 V4 页面，桩 fetch 模拟后端任务接口，验证提交 → 轮询 → 进度 → 结果 / 失败 / 任务丢失。
 *
 * 运行：node tests/v6/test_scan_jobs.js   （需要 typescript 包，CI 临时安装）
 */
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const ts = require('typescript');
const { loadPage } = require('./v4_page_harness');

const ROOT = path.resolve(__dirname, '..', '..');
const DIR = path.join(ROOT, 'v6', 'app-builder', 'server', 'modules', 'stock-analysis');

function compile(file, requireMap) {
  const out = ts.transpileModule(fs.readFileSync(path.join(DIR, file), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, strict: true },
    reportDiagnostics: true,
  });
  assert.strictEqual((out.diagnostics || []).length, 0, `${file} TS 诊断非空`);
  const mod = { exports: {} };
  const req = (name) => (name in requireMap ? requireMap[name] : require(name));
  vm.runInThisContext(`(function (module, exports, require) {${out.outputText}\n})`)(mod, mod.exports, req);
  return mod.exports;
}
const pure = compile('stock-analysis.pure.ts', {});
const { ScanJobManager } = compile('scan-jobs.ts', { './stock-analysis.pure': pure });
const { runProcess } = compile('run-process.ts', {});

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); passed++; console.log('  ✓ ' + name); }
  catch (e) { failed++; console.log('  ✗ ' + name + '\n      ' + (e && e.stack ? e.stack.split('\n').slice(0, 3).join('\n      ') : e)); }
}
const tick = () => new Promise(r => setImmediate(r));
async function until(cond, ms = 3000) {
  const t0 = Date.now();
  while (!cond()) { if (Date.now() - t0 > ms) throw new Error('等待超时'); await tick(); }
}
const codes = n => Array.from({ length: n }, (_, i) => String(600000 + i));

/** 可控的假 runner：记录并发峰值，按代码决定成功 / 失败 / 挂起（挂起的在收到中止后才结束，模拟进程被杀） */
function fakeRunner({ fail = new Set(), hang = new Set(), score = () => 70 } = {}) {
  const st = { active: 0, peak: 0, calls: [] };
  const run = async (code, signal) => {
    st.calls.push(code); st.active++; st.peak = Math.max(st.peak, st.active);
    try {
      if (hang.has(code)) {
        await new Promise((_, reject) => signal.addEventListener('abort', () => setTimeout(() => reject(new Error('killed')), 5), { once: true }));
      }
      await tick(); await tick();
      if (fail.has(code)) throw new Error(`Python 退出码 1：${code} 行情获取失败`);
      const s = score(code);
      return { code, bull_top: s === null ? null : { pid: 'w_bottom', score: s, confirmed: true }, bear_top: null };
    } finally { st.active--; }
  };
  return { run, st };
}

(async () => {
  console.log('后端 ScanJobManager');

  await test('200 只：立即返回任务 ID，全局并发不超过 3，全部完成', async () => {
    const r = fakeRunner();
    const m = new ScanJobManager(r.run, { maxParallel: 3 });
    const { job_id, total } = m.create(codes(200));
    assert.strictEqual(total, 200);
    assert.strictEqual(m.get(job_id).status, 'running');
    await until(() => m.get(job_id).status === 'done');
    const v = m.get(job_id);
    assert.strictEqual(r.st.peak, 3, '并发峰值应为 3');
    assert.deepStrictEqual([v.done, v.succeeded, v.failed_count, v.items.length], [200, 200, 0, 200]);
    assert.strictEqual(r.st.calls.length, 200);
  });

  await test('两个任务同时跑：全局并发仍不超过上限，两者轮流推进', async () => {
    const r = fakeRunner();
    const m = new ScanJobManager(r.run, { maxParallel: 2 });
    const a = m.create(codes(10)).job_id;
    const b = m.create(codes(10).map(c => String(Number(c) + 1000))).job_id;
    await until(() => m.get(a).status === 'done' && m.get(b).status === 'done');
    assert.strictEqual(r.st.peak, 2);
    assert.ok(r.st.calls.slice(0, 4).some(c => Number(c) >= 601000), '第二个任务应在第一个结束前就开始');
  });

  await test('输入校验：去重、非法代码、超过 200 只、空列表、同时运行任务数上限', async () => {
    const r = fakeRunner({ hang: new Set(codes(5)) });
    const m = new ScanJobManager(r.run, { maxActiveJobs: 1 });
    assert.strictEqual(m.create(['600000', ' 600000', '600000']).total, 1);
    assert.throws(() => m.create(['60000a']), /非法代码/);
    assert.throws(() => m.create(codes(201)), /最多 200 只/);
    assert.throws(() => m.create([]), /不能为空/);
    assert.throws(() => m.create('600000'), /不能为空/);
    assert.throws(() => m.create(['600001']), /已有 1 个扫描在运行/);
  });

  await test('单只失败 / 超时计入失败清单，带原因，不影响其它股票', async () => {
    const r = fakeRunner({ fail: new Set(['600001']), hang: new Set(['600002']) });
    const m = new ScanJobManager(r.run, { perStockTimeoutMs: 50 });
    const id = m.create(codes(5)).job_id;
    await until(() => m.get(id).status === 'done');
    const v = m.get(id);
    assert.strictEqual(v.succeeded, 3);
    const reasons = Object.fromEntries(v.failed.map(f => [f.code, f.reason]));
    assert.ok(/行情获取失败/.test(reasons['600001']));
    assert.ok(/超时/.test(reasons['600002']));
  });

  await test('筛选与排序：只看多头 / 最低分数，按已确认多头分数从高到低', async () => {
    const scores = { '600000': 50, '600001': 90, '600002': null, '600003': 75 };
    const r = fakeRunner({ score: c => scores[c] });
    const m = new ScanJobManager(r.run);
    const id = m.create(Object.keys(scores), { minScore: 60, onlyBull: true }).job_id;
    await until(() => m.get(id).status === 'done');
    const v = m.get(id);
    assert.deepStrictEqual(v.items.map(i => i.code), ['600001', '600003']);
    assert.deepStrictEqual([v.matched, v.filtered_out, v.succeeded], [2, 2, 4]);
  });

  await test('取消：未启动的不再分析，状态为 cancelled', async () => {
    const r = fakeRunner();
    const m = new ScanJobManager(r.run, { maxParallel: 1 });
    const id = m.create(codes(50)).job_id;
    await until(() => m.get(id).done >= 2);
    assert.strictEqual(m.cancel(id), true);
    await until(() => m.get(id).finished_at !== null);
    const v = m.get(id);
    assert.strictEqual(v.status, 'cancelled');
    assert.ok(v.done < 50 && r.st.calls.length === v.done, `取消后仍在分析：${r.st.calls.length}`);
    assert.strictEqual(m.cancel('no-such-id'), false);
  });

  await test('已结束任务过期清理；未知任务返回 null', async () => {
    let now = 1000;
    const r = fakeRunner();
    const m = new ScanJobManager(r.run, { retentionMs: 60000, now: () => now });
    const id = m.create(codes(2)).job_id;
    await until(() => m.get(id).status === 'done');
    now += 59000; assert.ok(m.get(id));
    now += 2000; assert.strictEqual(m.get(id), null);
    assert.strictEqual(m.get('nope'), null);
  });

  console.log('QA R71-2：超时终止底层分析，确认结束后才释放名额');

  await test('连续超时：真实在跑的 runner 峰值不超过上限，全部记为超时失败', async () => {
    const r = fakeRunner({ hang: new Set(codes(6)) });
    const m = new ScanJobManager(r.run, { maxParallel: 2, perStockTimeoutMs: 10 });
    const id = m.create(codes(6)).job_id;
    let peakManager = 0;
    while (m.get(id).status === 'running') { peakManager = Math.max(peakManager, m.activeCount()); await tick(); }
    const v = m.get(id);
    assert.strictEqual(r.st.peak, 2, `实际在跑峰值 ${r.st.peak}`);
    assert.ok(peakManager <= 2);
    assert.strictEqual(r.st.active, 0, '仍有 runner 未结束');
    assert.strictEqual(v.failed.length, 6);
    assert.ok(v.failed.every(f => /超时.*已终止/.test(f.reason)));
  });

  await test('runner 无视中止、一直不结束：名额一直占着，不会超过上限启动新股票（硬上限）', async () => {
    const st = { active: 0, peak: 0, calls: 0 };
    const run = () => { st.calls++; st.active++; st.peak = Math.max(st.peak, st.active); return new Promise(() => {}); };
    const m = new ScanJobManager(run, { maxParallel: 2, perStockTimeoutMs: 5 });
    const id = m.create(codes(5)).job_id;
    await new Promise(r => setTimeout(r, 50));
    assert.deepStrictEqual([st.calls, st.peak, m.activeCount()], [2, 2, 2]);
    assert.strictEqual(m.get(id).status, 'running');
    assert.strictEqual(m.get(id).pending, 3);
  });

  await test('真实子进程：超时 SIGKILL，确认退出后才启动下一只；并发不超过 2，结束后无遗留进程', async () => {
    const pids = [];
    let live = 0, peak = 0;
    const run = async (code, signal) => {
      live++; peak = Math.max(peak, live);
      try {
        const res = await runProcess('sleep', ['30'], { signal });
        pids.push(res.pid);
        throw new Error(`killed=${res.killed}`);
      } finally { live--; }
    };
    const m = new ScanJobManager(run, { maxParallel: 2, perStockTimeoutMs: 150 });
    const id = m.create(codes(5)).job_id;
    await until(() => m.get(id).status === 'done', 10000);
    const v = m.get(id);
    assert.strictEqual(peak, 2, `并发峰值 ${peak}`);
    assert.strictEqual(pids.length, 5);
    const alive = pids.filter(pid => { try { process.kill(pid, 0); return true; } catch { return false; } });
    assert.deepStrictEqual(alive, [], `遗留进程：${alive}`);
    assert.ok(v.failed.every(f => /超时/.test(f.reason)));
  });

  await test('runProcess：正常输出、超时 SIGKILL、中止 SIGKILL 都在进程退出后才返回', async () => {
    const ok = await runProcess(process.execPath, ['-e', 'process.stdout.write("hi")']);
    assert.deepStrictEqual([ok.stdout, ok.exitCode, ok.killed], ['hi', 0, false]);
    const to = await runProcess('sleep', ['30'], { timeoutMs: 50 });
    assert.deepStrictEqual([to.killed, to.signal], ['timeout', 'SIGKILL']);
    const ac = new AbortController();
    setTimeout(() => ac.abort(), 30);
    const ab = await runProcess('sleep', ['30'], { signal: ac.signal });
    assert.deepStrictEqual([ab.killed, ab.signal], ['aborted', 'SIGKILL']);
    for (const pid of [to.pid, ab.pid]) assert.throws(() => process.kill(pid, 0));
  });

  console.log('前端：实际部署的 V4 页面');

  /** 模拟后端任务接口：每次 GET 推进 step 只 */
  function fakeBackend({ items, failed = [], total, step = 2, lose = false }) {
    const calls = [];
    let done = 0;
    const json = (status, body) => ({ ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body) });
    const fetch = async (url, init = {}) => {
      calls.push(`${init.method || 'GET'} ${url.replace(/^.*\/api/, '/api')}`);
      if (/\/scan\/jobs$/.test(url) && init.method === 'POST') return json(201, { job_id: 'job-1', total });
      if (/\/scan\/jobs\/job-1$/.test(url) && (init.method || 'GET') === 'GET') {
        if (lose && done > 0) return json(404, { message: '扫描任务不存在或已过期（服务重启会清空任务），请重新扫描' });
        done = Math.min(total, done + step);
        const finished = done >= total;
        return json(200, { job_id: 'job-1', status: finished ? 'done' : 'running', finished_at: finished ? '2026-10-23T00:00:00Z' : null, total, done,
          succeeded: items.length, matched: items.length, failed_count: finished ? failed.length : 0,
          items: finished ? items : [], failed: finished ? failed : [] });
      }
      return json(404, { message: 'unexpected ' + url });
    };
    return { fetch, calls };
  }
  const batchItem = (code, score) => ({
    code, name: code, as_of: '2026-10-13', price: 10, pct_num: 1.2, signal_rule_version: 'signals-1',
    bull_top: score === null ? null : { pid: 'w_bottom', name: 'W底', stage: 'post_break', score, confirmed: true },
    bear_top: null,
  });

  await test('市场扫描：提交任务 → 轮询进度 → 渲染 Python 结果与失败清单', async () => {
    const be = fakeBackend({ total: 5, items: [batchItem('600001', 90), batchItem('600003', 75)],
      failed: [{ code: '600002', reason: '分析超时（>35 秒）' }] });
    const p = loadPage({ fetch: be.fetch, immediateTimers: true, exports: ['startMarketScan'] });
    const doc = p.sandbox.document;
    doc.querySelectorAll = (sel) => sel === '.sector-checkbox:checked' ? [{ value: 'S' }] : [];
    p.sandbox.getScanScope = () => ({ ids: [], customCodes: ['600001', '600002', '600003', '600004', '600005'], customNames: ['S'], total: 5 });
    doc.getElementById('scanMinScore').value = '0';
    doc.getElementById('scanSignalType').value = 'all';
    await p.x.startMarketScan();
    assert.deepStrictEqual(be.calls.filter(c => c.startsWith('POST')), ['POST /api/scan/jobs']);
    assert.ok(!be.calls.some(c => c === 'POST /api/scan'), '不应再调用同步 /api/scan');
    const status = doc.getElementById('scanStatus').innerHTML;
    assert.ok(/扫描完成/.test(status) && /失败1只/.test(status) && /分析超时/.test(status), status);
    assert.strictEqual(doc.getElementById('scanProgressBar').style.width, '100%');
    assert.ok(/600001/.test(doc.getElementById('scanResults').innerHTML), '结果表应包含 600001');
  });

  await test('任务丢失（服务重启，404）：明确报错，不展示旧结果', async () => {
    const be = fakeBackend({ total: 10, items: [], lose: true });
    const p = loadPage({ fetch: be.fetch, immediateTimers: true, exports: ['startMarketScan'] });
    const doc = p.sandbox.document;
    doc.querySelectorAll = (sel) => sel === '.sector-checkbox:checked' ? [{ value: 'S' }] : [];
    p.sandbox.getScanScope = () => ({ ids: [], customCodes: codes(10), customNames: ['S'], total: 10 });
    doc.getElementById('scanMinScore').value = '0';
    doc.getElementById('scanSignalType').value = 'all';
    await p.x.startMarketScan();
    const status = doc.getElementById('scanStatus').innerHTML;
    assert.ok(/扫描失败/.test(status) && /请重新扫描/.test(status), status);
    assert.strictEqual(doc.getElementById('scanResults').innerHTML, '');
  });

  await test('自选股批量分析：走后台任务，结果按 Python 已确认主导形态展示', async () => {
    const be = fakeBackend({ total: 2, items: [batchItem('600001', 88), batchItem('600002', null)] });
    const p = loadPage({ fetch: be.fetch, immediateTimers: true, exports: ['analyzeAllWatchlist'] });
    p.sandbox.getWatchlist = () => ['600001', '600002'];
    p.sandbox.renderWatchlistTags = () => {};
    let rendered = 0;
    p.sandbox.renderWatchlistTable = () => { rendered++; };
    await p.x.analyzeAllWatchlist();
    assert.ok(be.calls.includes('POST /api/scan/jobs'));
    const status = p.sandbox.document.getElementById('batchStatus').innerHTML;
    assert.ok(/分析完成/.test(status) && /成功2只/.test(status), status);
    const wr = vm.runInContext('watchlistResults', p.sandbox);
    assert.strictEqual(wr['600001'].bull_top.score, 88);
    assert.strictEqual(wr['600002'].bull_top, null);
    assert.ok(rendered >= 2);
  });

  await test('QA R71-1：取消后页面继续等在途股票，最终展示其结果；未启动的不再分析', async () => {
    // 页面 → 桩 fetch → 真实 ScanJobManager；第一只股票挂起，直到测试放行
    let release;
    const gate = new Promise(r => { release = r; });
    const calls = [];
    const runner = async (code) => {
      calls.push(code);
      if (code === '600001') await gate;
      return batchItem(code, 80);
    };
    const m = new ScanJobManager(runner, { maxParallel: 1 });
    const res = (status, body) => ({ ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body) });
    let gets = 0;
    const fetch = async (url, init = {}) => {
      const method = init.method || 'GET';
      const mm = url.match(/\/scan\/jobs(?:\/([^/]+))?$/);
      if (!mm) return res(404, {});
      if (method === 'POST') return res(201, m.create(JSON.parse(init.body).codes, {}));
      if (method === 'DELETE') { m.cancel(mm[1]); return res(200, m.get(mm[1])); }
      gets++;
      const v = m.get(mm[1]);
      return v ? res(200, JSON.parse(JSON.stringify(v))) : res(404, { message: 'gone' });
    };
    const p = loadPage({ fetch, immediateTimers: true, exports: ['startMarketScan', 'cancelMarketScan'] });
    const doc = p.sandbox.document;
    doc.querySelectorAll = (sel) => sel === '.sector-checkbox:checked' ? [{ value: 'S' }] : [];
    p.sandbox.getScanScope = () => ({ ids: [], customCodes: ['600001', '600002', '600003'], customNames: ['S'], total: 3 });
    doc.getElementById('scanMinScore').value = '0';
    doc.getElementById('scanSignalType').value = 'all';
    let finished = false;
    const scan = p.x.startMarketScan().then(() => { finished = true; });
    await until(() => calls.length === 1 && vm.runInContext('currentScanJobId', p.sandbox));
    await p.x.cancelMarketScan();
    const getsAtCancel = gets;
    await until(() => gets > getsAtCancel + 3);
    assert.strictEqual(finished, false, '取消后、在途股票结束前，页面不应结束轮询');
    assert.ok(/已请求停止.*在途/.test(doc.getElementById('scanStatus').innerHTML), doc.getElementById('scanStatus').innerHTML);
    release();
    await scan;
    assert.deepStrictEqual(calls, ['600001'], '未启动的股票不应再分析');
    const status = doc.getElementById('scanStatus').innerHTML;
    assert.ok(/扫描已停止/.test(status) && /已分析 1 只/.test(status), status);
    assert.ok(/600001/.test(doc.getElementById('scanResults').innerHTML), '在途股票的最终结果应展示');
  });

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
