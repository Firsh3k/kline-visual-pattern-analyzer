/**
 * FIR 美股阶段1 — 个股验收证据（确定性，不访问网络）：
 *   1) Core ↔ API 逐字段对照（固定 fixture + 固定时间，us_acceptance_make_docs.py 生成）
 *   2) API ↔ 页面：实际部署的 V4 页面 doAnalyze 渲染，形态/阶段/分数/确认状态与 doc.signals 一致
 *   3) 快速切换响应乱序：002436→AAPL 与反向，旧请求成功/失败/结束都不得覆盖当前页面
 *   4) 失败提示：上游失败 / 非 real_core 引擎 → 清楚错误横幅，不展示推测结果
 *
 * 运行：node tests/v6/test_us_acceptance.js
 */
'use strict';
const assert = require('assert');
const { execFileSync } = require('child_process');
const path = require('path');
const { loadPage, ROOT } = require('./v4_page_harness');

const EXPORTS = ['doAnalyze', 'fetchPythonDoc', 'docToV4Data', 'buildMatchesFromDoc', 'pickDominant',
  'renderAnalysis', 'PATTERNS', 'BEAR_PATTERNS', 'addStock', 'addCustomSector', 'isUSCode', 'isCNCode'];
const PY = process.env.PYTHON || 'python3';
const DOCS = JSON.parse(execFileSync(PY, [path.join(ROOT, 'tests', 'v6', 'us_acceptance_make_docs.py')],
  { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 }));

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); passed++; console.log('  ✓ ' + name); }
  catch (e) { failed++; console.log('  ✗ ' + name + '\n      ' + (e && e.message ? e.message.split('\n').join('\n      ') : e)); }
}
const flush = () => new Promise(r => setImmediate(r));
async function settle(n = 8) { for (let i = 0; i < n; i++) await flush(); }

function nameOf(id) {
  const p = loadPage({ exports: EXPORTS });
  const all = [...(p.x.PATTERNS || []), ...(p.x.BEAR_PATTERNS || [])];
  const hit = all.find(x => x.id === id);
  return hit ? hit.name : id;
}

/** 受控 fetch：按 /analyze/{code} 路由到外部可决议的 deferred */
function controlledFetch() {
  const pending = {};
  const fetchStub = async (url) => {
    const m = String(url).match(/\/analyze\/([A-Za-z0-9]+)/);
    const code = m ? m[1] : 'unknown';
    if (!pending[code]) {
      let resolve;
      const promise = new Promise(r => { resolve = r; });
      pending[code] = { promise, resolve };
    }
    return pending[code].promise;
  };
  return { pending, fetchStub };
}
const okResp = doc => ({ ok: true, status: 200, json: async () => doc });

function racePage() {
  const { pending, fetchStub } = controlledFetch();
  const p = loadPage({ exports: EXPORTS, fetch: fetchStub, canvasIds: ['chartFull', 'chart60'] });
  p.sandbox.fetchSinaKlineJSONP = async () => null; // 大盘环境桩：不影响个股竞态判定
  return { p, pending };
}
const areaHtml = p => p.sandbox.document.getElementById('resultArea').innerHTML;
const setCode = (p, c) => { p.sandbox.document.getElementById('codeInput').value = c; };

(async () => {
  console.log('一、Core ↔ API 逐字段对照（固定行情 + 固定时间）');

  for (const ticker of ['AAPL', 'NVDA']) {
    const { core, api, fixture } = DOCS[ticker];
    await test(`${ticker} ${fixture.name}：result_sha256 / signals_sha256 / data_hash 完全一致`, () => {
      assert.strictEqual(api.core.result_sha256, core.core.result_sha256);
      assert.strictEqual(api.signals.signals_sha256, core.signals.signals_sha256);
      assert.strictEqual(api.input.data_hash, core.input.data_hash);
    });
    await test(`${ticker}：as_of / session_state / 日历证据 / 250窗口 一致`, () => {
      assert.strictEqual(api.as_of, core.as_of);
      assert.strictEqual(api.as_of, fixture.last_day);
      assert.strictEqual(api.session_state, core.session_state);
      assert.strictEqual(api.session_state, 'close_confirmed');
      assert.strictEqual(api.calendar.calendar_id, core.calendar.calendar_id);
      assert.strictEqual(api.calendar.status, 'verified');
      assert.strictEqual(api.calendar.expected_trade_date, core.calendar.expected_trade_date);
      assert.strictEqual(api.input.bar_count, 250);
      assert.strictEqual(api.engine.core_version, core.engine.core_version);
    });
    await test(`${ticker}：8形态 latest 分数/阶段/确认 与 dominant 逐项一致`, () => {
      for (const pid of core.engine.pattern_order) {
        const c = core.signals.latest[pid] || {}, a = api.signals.latest[pid] || {};
        assert.strictEqual(a.score, c.score, `${pid}.score`);
        assert.strictEqual(a.stage, c.stage, `${pid}.stage`);
        assert.strictEqual(a.confirmed, c.confirmed, `${pid}.confirmed`);
      }
      assert.deepStrictEqual(api.signals.dominant, core.signals.dominant);
    });
    await test(`${ticker}：API 兼容层（summary/engine.analyzer/klines）与 Core 同源`, () => {
      assert.strictEqual(api.engine.analyzer, 'real_core');
      assert.strictEqual(api.data_length, 250);
      assert.strictEqual(api.klines.length, 250);
      assert.strictEqual(api.latest_close, api.bars[api.bars.length - 1][4]);
      assert.strictEqual(api.summary.as_of, core.as_of);
    });
  }

  console.log('二、API ↔ 页面：doAnalyze 渲染与 doc.signals 一致');

  for (const ticker of ['AAPL', 'NVDA']) {
    const api = DOCS[ticker].api;
    await test(`${ticker}：页面主导形态/分数/确认状态来自 Python signals`, async () => {
      const p = loadPage({ exports: EXPORTS, canvasIds: ['chartFull', 'chart60'],
        fetch: async () => okResp(api) });
      p.sandbox.fetchSinaKlineJSONP = async () => null;
      setCode(p, ticker);
      await p.x.doAnalyze();
      await settle();
      const html = areaHtml(p);
      const dom = api.signals.dominant.overall;
      assert.ok(dom, '应有主导结构');
      assert.ok(html.includes(nameOf(dom.pattern_id)), `页面应展示主导形态名 ${nameOf(dom.pattern_id)}`);
      const latest = api.signals.latest[dom.pattern_id];
      assert.ok(html.includes(`${latest.score}/100`), `页面应展示信号强度 ${latest.score}/100`);
      assert.ok(html.includes(dom.confirmed ? '已确认' : '未确认'), '确认状态应如实展示');
      // 页面自己的适配器与 signals 一致（不另立规则）
      const { matches, bearMatches } = p.x.buildMatchesFromDoc(api);
      const pd = p.x.pickDominant(api, matches, bearMatches);
      assert.strictEqual(pd.id, dom.pattern_id);
      assert.strictEqual(pd.confirmed, dom.confirmed);
    });
  }

  console.log('三、快速切换响应乱序：旧响应不得覆盖当前页面');

  await test('002436 → AAPL：AAPL 先返回，002436 后返回 → 页面保留 AAPL', async () => {
    const { p, pending } = racePage();
    setCode(p, '002436'); const first = p.x.doAnalyze();
    setCode(p, 'AAPL'); const second = p.x.doAnalyze();
    pending['AAPL'].resolve(okResp(DOCS.AAPL.api));
    await second; await settle();
    assert.ok(areaHtml(p).includes('AAPL') || areaHtml(p).includes(nameOf(DOCS.AAPL.api.signals.dominant.overall.pattern_id)), 'AAPL 应已渲染');
    const before = areaHtml(p);
    pending['002436'].resolve(okResp(DOCS['002436'].core));
    await first; await settle();
    assert.strictEqual(areaHtml(p), before, '旧请求成功返回不得覆盖当前页面');
    assert.ok(!areaHtml(p).includes('兴森科技'), '不得出现旧标的名称');
  });

  await test('反向 AAPL → 002436：002436 先返回，AAPL 后返回 → 页面保留 002436', async () => {
    const { p, pending } = racePage();
    setCode(p, 'AAPL'); const first = p.x.doAnalyze();
    setCode(p, '002436'); const second = p.x.doAnalyze();
    pending['002436'].resolve(okResp(DOCS['002436'].core));
    await second; await settle();
    const before = areaHtml(p);
    assert.ok(before.length > 0, '002436 应已渲染');
    pending['AAPL'].resolve(okResp(DOCS.AAPL.api));
    await first; await settle();
    assert.strictEqual(areaHtml(p), before, '反向切换旧响应同样不得覆盖');
  });

  await test('旧请求失败（网络错误）不得覆盖已成功的新页面', async () => {
    const { p, pending } = racePage();
    setCode(p, '002436'); const first = p.x.doAnalyze();
    setCode(p, 'AAPL'); const second = p.x.doAnalyze();
    pending['AAPL'].resolve(okResp(DOCS.AAPL.api));
    await second; await settle();
    const before = areaHtml(p);
    pending['002436'].resolve({ ok: false, status: 500, json: async () => ({ message: '上游行情失败' }) });
    await first; await settle();
    assert.strictEqual(areaHtml(p), before, '旧请求失败横幅不得覆盖新结果');
  });

  await test('旧请求异常（fetch reject）不得覆盖已成功的新页面', async () => {
    const { p, pending } = racePage();
    setCode(p, '002436'); const first = p.x.doAnalyze();
    setCode(p, 'AAPL'); const second = p.x.doAnalyze();
    pending['AAPL'].resolve(okResp(DOCS.AAPL.api));
    await second; await settle();
    const before = areaHtml(p);
    pending['002436'].resolve(Promise.reject(new Error('connection reset')));
    await first; await settle();
    assert.strictEqual(areaHtml(p), before, '旧请求异常不得覆盖新结果');
  });

  await test('旧请求结束时不得把仍在分析中的新请求按钮复位', async () => {
    const { p, pending } = racePage();
    setCode(p, '002436'); const first = p.x.doAnalyze();
    setCode(p, 'AAPL'); const second = p.x.doAnalyze();
    pending['002436'].resolve(okResp(DOCS['002436'].core));
    await first; await settle();
    const btn = p.sandbox.document.getElementById('analyzeBtn');
    assert.strictEqual(btn.disabled, true, '新请求仍在途，按钮应保持禁用');
    pending['AAPL'].resolve(okResp(DOCS.AAPL.api));
    await second; await settle();
    assert.strictEqual(btn.disabled, false, '最新请求完成后按钮恢复');
  });

  console.log('四、失败提示：清楚错误，不展示推测结果');

  await test('上游失败 → 错误横幅含原因，且无本地兜底结果', async () => {
    const p = loadPage({ exports: EXPORTS, canvasIds: ['chartFull', 'chart60'],
      fetch: async () => ({ ok: false, status: 502, json: async () => ({ message: '行情获取失败（fail-closed，未降级）：东方财富全部主机不可用' }) }) });
    p.sandbox.fetchSinaKlineJSONP = async () => null;
    setCode(p, 'AAPL');
    await p.x.doAnalyze(); await settle();
    const html = areaHtml(p);
    assert.ok(html.includes('行情获取失败'), '应展示清楚失败原因');
    assert.ok(html.includes('不使用任何本地/JS 算法兜底'), '应声明无兜底');
    assert.ok(!html.includes('平台突破') && !html.includes('M头'), '失败时不得展示形态结论');
  });

  await test('非 real_core 引擎响应 → 拒绝展示', async () => {
    const fake = JSON.parse(JSON.stringify(DOCS.AAPL.api));
    fake.engine.analyzer = 'js_fallback';
    const p = loadPage({ exports: EXPORTS, fetch: async () => okResp(fake) });
    p.sandbox.fetchSinaKlineJSONP = async () => null;
    setCode(p, 'AAPL');
    await p.x.doAnalyze(); await settle();
    const html = areaHtml(p);
    assert.ok(html.includes('引擎不是 Python Canonical Core'), '应拒绝非 real_core 引擎');
  });

  console.log('五、页面范围：美股仅个股分析，隐藏大盘依赖项；自选股/自定义板块限 A 股');

  await test('美股个股页隐藏相对强度（RS）卡片与逆大盘项、无缺大盘警告', async () => {
    const p = loadPage({ exports: EXPORTS, canvasIds: ['chartFull', 'chart60'], fetch: async () => okResp(DOCS.AAPL.api) });
    p.sandbox.fetchSinaKlineJSONP = async () => { throw new Error('美股不应取上证指数'); };
    setCode(p, 'AAPL');
    await p.x.doAnalyze(); await settle();
    const html = areaHtml(p);
    assert.ok(html.includes(nameOf(DOCS.AAPL.api.signals.dominant.overall.pattern_id)), '美股个股形态仍正常展示');
    assert.ok(!html.includes('相对强度（RS）'), '美股应隐藏相对强度卡片');
    assert.ok(!html.includes('逆大盘'), '美股应隐藏逆大盘风险项');
    assert.ok(!html.includes('大盘环境适配'), '美股应隐藏大盘环境适配维度');
    assert.ok(!html.includes('缺少大盘环境维度'), '美股不取大盘，不应显示缺大盘警告');
    assert.ok(!html.includes('大盘环境（上证指数）'), '美股应整卡隐藏大盘环境卡');
    assert.ok(!html.includes('大盘数据获取中'), '美股不应残留永久悬挂的"大盘数据获取中"');
  });

  await test('A 股个股页保留相对强度（RS）卡片（保持原样）', async () => {
    const p = loadPage({ exports: EXPORTS, canvasIds: ['chartFull', 'chart60'] });
    const doc = DOCS['002436'].core;
    const data = p.x.docToV4Data(doc);
    // 直接验证渲染层：A 股（isUS=false）即使大盘缺失也保留 RS 卡片（显示“大盘不可用”）
    p.x.renderAnalysis({ name: doc.name || '002436', code: '002436', data }, null, null, { doc, isUS: false });
    await settle();
    const html = areaHtml(p);
    assert.ok(html.includes('相对强度（RS）'), 'A 股应保留相对强度卡片');
    assert.ok(html.includes('大盘环境（上证指数）'), 'A 股应保留大盘环境卡（保持原样）');
  });

  await test('自选股输入框示例仅 A 股（去掉 AAPL 示例）', () => {
    const fs = require('fs');
    const page = process.env.V4_PAGE || require('path').join(ROOT, 'v6', 'app-builder', 'client', 'public', 'v4', 'index.html');
    const html = fs.readFileSync(page, 'utf8');
    const m = /id="addStockInput"[^>]*placeholder="([^"]*)"/.exec(html);
    assert.ok(m, '页面应有 addStockInput 输入框');
    assert.ok(!m[1].includes('AAPL'), '自选股入口只收 A 股，示例不应再含 AAPL');
    assert.ok(m[1].includes('A 股'), '示例应明示 A 股');
  });

  await test('isUSCode / isCNCode 判定正确', () => {
    const p = loadPage({ exports: EXPORTS });
    assert.strictEqual(p.x.isUSCode('AAPL'), true);
    assert.strictEqual(p.x.isUSCode('BRK.B'), true);
    assert.strictEqual(p.x.isUSCode('600519'), false);
    assert.strictEqual(p.x.isCNCode('600519'), true);
    assert.strictEqual(p.x.isCNCode('002436'), true);
    assert.strictEqual(p.x.isCNCode('AAPL'), false);
  });

  await test('自选股 addStock 拒绝美股代码（后端只收六位数字）', async () => {
    const p = loadPage({ exports: EXPORTS });
    const alerts = [];
    p.sandbox.alert = m => alerts.push(String(m));
    p.sandbox.document.getElementById('addStockInput').value = 'AAPL';
    await p.x.addStock();
    assert.ok(alerts.some(m => m.includes('自选股仅支持 A 股')), '应提示自选股仅限 A 股');
  });

  await test('自定义板块 addCustomSector 拒绝美股代码', async () => {
    const p = loadPage({ exports: EXPORTS });
    const alerts = [];
    p.sandbox.alert = m => alerts.push(String(m));
    p.sandbox.document.getElementById('customSectorName').value = '测试板块';
    p.sandbox.document.getElementById('customSectorCodes').value = 'AAPL,600519';
    await p.x.addCustomSector();
    assert.ok(alerts.some(m => m.includes('自定义板块仅支持 A 股') && m.includes('AAPL')), '应拒绝并点名美股代码');
  });

  console.log(`\n结果: ${passed} 通过, ${failed} 失败`);
  process.exit(failed ? 1 : 0);
})();