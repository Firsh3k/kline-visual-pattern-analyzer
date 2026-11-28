/**
 * 旧版单文件线 — 市场切换串结果竞态回归（确定性，不访问网络）。
 *
 * 复现：请求 002436 → 切换到美股并请求 AAPL → AAPL 先返回 → 002436 后返回。
 * 预期：页面保留 AAPL；旧请求的成功、失败或结束状态都不能覆盖当前市场。
 * 含反向切换（AAPL → 002436）与按钮状态守卫。
 *
 * 直接加载 kline_analyzer.html 的真实生产 doAnalyze / switchMarket（不复制实现），
 * 仅桩 fetchKline / fetchMarketIndexData 控制响应时序。
 *
 * 运行：node tests/test_market_switch_race.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const HTML = process.env.KLINE_HTML || path.join(__dirname, '..', 'kline_analyzer.html');
const EXPORTS = ['doAnalyze', 'switchMarket', 'isUSMarket', 'marketCfg', 'renderAnalysis'];
// 版本徽章等辅助脚本的异步失败（fetch 桩拒绝等）不得 crash 测试进程
process.on('unhandledRejection', () => {});

function recordingCanvas() {
  const ctx = new Proxy({}, {
    get(t, k) { if (k in t) return t[k]; return (...a) => {}; },
    set(t, k, v) { t[k] = v; return true; },
  });
  return { width: 800, height: 320, style: {}, getContext: () => ctx,
    getBoundingClientRect: () => ({ width: 800, height: 320, left: 0, top: 0 }) };
}
function fakeElement(id) {
  return {
    id, style: {}, dataset: {}, value: '', checked: false, disabled: false,
    textContent: '', innerHTML: '', className: '', children: [],
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    addEventListener() {}, removeEventListener() {}, appendChild(c) { this.children.push(c); return c; },
    removeChild() {}, setAttribute() {}, getAttribute: () => null,
    querySelector: () => fakeElement(), querySelectorAll: () => [],
    focus() {}, blur() {}, click() {}, scrollIntoView() {},
    getBoundingClientRect: () => ({ width: 800, height: 320, left: 0, top: 0 }),
    getContext: () => recordingCanvas().getContext(),
  };
}

function loadPage() {
  const html = fs.readFileSync(HTML, 'utf8');
  const scripts = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
  const elements = {};
  const canvasIds = ['chartFull', 'chart60'];
  const document = {
    cookie: '',
    getElementById: id => {
      if (!elements[id]) elements[id] = canvasIds.includes(id) ? recordingCanvas() : fakeElement(id);
      return elements[id];
    },
    querySelector: () => fakeElement(), querySelectorAll: () => [],
    createElement: tag => fakeElement(tag), createTextNode: t => ({ t }),
    addEventListener() {}, removeEventListener() {},
    body: fakeElement('body'), head: fakeElement('head'), documentElement: fakeElement('html'),
    readyState: 'complete',
  };
  const storage = {};
  const sandbox = {
    console: { log() {}, warn() {}, error() {}, info() {} },
    document,
    localStorage: { getItem: k => (k in storage ? storage[k] : null), setItem: (k, v) => { storage[k] = String(v); }, removeItem: k => { delete storage[k]; }, clear: () => {} },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    navigator: { userAgent: 'node' },
    location: { href: 'https://example.test/', pathname: '/', search: '', hash: '', origin: 'https://example.test', protocol: 'https:', host: 'example.test' },
    history: { pushState() {}, replaceState() {} },
    alert() {}, confirm: () => false, prompt: () => null,
    setTimeout: (fn) => { setImmediate(() => { try { fn(); } catch (e) { /* 辅助脚本定时器容错 */ } }); return 0; }, clearTimeout() {},
    setInterval: () => 0, clearInterval() {}, requestAnimationFrame: () => 0,
    fetch: async () => { throw new Error('fetch 未桩'); },
    URL, URLSearchParams, TextEncoder, TextDecoder, Intl, Date, Math, JSON, Promise, Set, Map,
    devicePixelRatio: 1, addEventListener() {}, removeEventListener() {},
    matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
    atob: s => Buffer.from(s, 'base64').toString('binary'), btoa: s => Buffer.from(s, 'binary').toString('base64'),
  };
  sandbox.window = sandbox; sandbox.self = sandbox;
  vm.createContext(sandbox);
  const tail = '\n;globalThis.__exports = {' + EXPORTS.map(n => `${n}: typeof ${n} !== 'undefined' ? ${n} : undefined`).join(',') + '};';
  // 逐脚本容错执行：版本徽章等辅助脚本在 mock DOM 下可能抛错（既有 FIR-21 同类问题），
  // 不得中断主应用脚本；函数声明提升保证生产函数可用。
  for (const s of scripts) {
    try { vm.runInContext(s, sandbox, { filename: 'kline_analyzer.html' }); } catch (e) { /* 辅助脚本容错 */ }
  }
  vm.runInContext(tail, sandbox, { filename: 'exports' });
  return { sandbox, x: sandbox.__exports, elements };
}

/** 合成 250 根温和上升 OHLC（够 renderAnalysis 用） */
function bars(n = 250) {
  const out = []; let p = 100;
  const d0 = new Date('2025-11-01');
  for (let i = 0; i < n; i++) {
    p += 0.1;
    const d = new Date(d0.getTime() + i * 86400000).toISOString().slice(0, 10);
    out.push({ date: d, open: p - 0.2, high: p + 0.5, low: p - 0.6, close: p, volume: 1e7 });
  }
  return out;
}
const flush = () => new Promise(r => setImmediate(r));
async function settle(n = 10) { for (let i = 0; i < n; i++) await flush(); }

function controlled() {
  const pending = {};
  const fetchKline = (code) => {
    const c = String(code).toUpperCase();
    if (!pending[c]) { let resolve, reject; const promise = new Promise((res, rej) => { resolve = res; reject = rej; }); pending[c] = { promise, resolve, reject }; }
    return pending[c].promise;
  };
  return { pending, fetchKline };
}

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); passed++; console.log('  ✓ ' + name); }
  catch (e) { failed++; console.log('  ✗ ' + name + '\n      ' + (e && e.message ? e.message.split('\n').join('\n      ') : e)); }
}

function racePage() {
  const { pending, fetchKline } = controlled();
  const p = loadPage();
  p.sandbox.fetchKline = fetchKline;
  p.sandbox.fetchMarketIndexData = async () => null;
  return { p, pending };
}
const areaHtml = p => p.sandbox.document.getElementById('resultArea').innerHTML;
const setCode = (p, c) => { p.sandbox.document.getElementById('codeInput').value = c; };
const okResult = code => ({ name: code === 'AAPL' ? '苹果' : '兴森科技', code, data: bars() });

(async () => {
  console.log('市场切换串结果竞态（真实生产 doAnalyze / switchMarket）');

  await test('002436 → 切美股 → AAPL：AAPL 先返回，002436 后返回 → 页面保留 AAPL', async () => {
    const { p, pending } = racePage();
    assert.strictEqual(p.x.isUSMarket(), false, '初始应为A股');
    setCode(p, '002436'); const first = p.x.doAnalyze();
    p.x.switchMarket('us');
    setCode(p, 'AAPL'); const second = p.x.doAnalyze();
    pending['AAPL'].resolve(okResult('AAPL'));
    await second; await settle();
    assert.ok(areaHtml(p).includes('AAPL'), 'AAPL 应已渲染');
    const before = areaHtml(p);
    pending['002436'].resolve(okResult('002436'));
    await first; await settle();
    assert.strictEqual(areaHtml(p), before, '旧市场请求成功返回不得覆盖当前页面');
    assert.ok(!areaHtml(p).includes('兴森科技'), '不得出现旧市场标的');
  });

  await test('反向：AAPL → 切A股 → 002436：002436 先返回，AAPL 后返回 → 页面保留 002436', async () => {
    const { p, pending } = racePage();
    p.x.switchMarket('us');
    setCode(p, 'AAPL'); const first = p.x.doAnalyze();
    p.x.switchMarket('cn');
    setCode(p, '002436'); const second = p.x.doAnalyze();
    pending['002436'].resolve(okResult('002436'));
    await second; await settle();
    const before = areaHtml(p);
    assert.ok(before.includes('002436'), '002436 应已渲染');
    pending['AAPL'].resolve(okResult('AAPL'));
    await first; await settle();
    assert.strictEqual(areaHtml(p), before, '反向切换旧响应不得覆盖');
    assert.ok(!areaHtml(p).includes('苹果'), '不得出现旧市场标的名称');
  });

  await test('旧请求失败不得覆盖已成功的新市场页面', async () => {
    const { p, pending } = racePage();
    setCode(p, '002436'); const first = p.x.doAnalyze();
    p.x.switchMarket('us');
    setCode(p, 'AAPL'); const second = p.x.doAnalyze();
    pending['AAPL'].resolve(okResult('AAPL'));
    await second; await settle();
    const before = areaHtml(p);
    pending['002436'].reject(new Error('新浪接口超时'));
    await first.catch(() => {}); await settle();
    assert.strictEqual(areaHtml(p), before, '旧请求失败横幅不得覆盖新结果');
  });

  await test('切换市场即作废在途请求（不发起新分析也不得渲染旧结果）', async () => {
    const { p, pending } = racePage();
    setCode(p, '002436'); const first = p.x.doAnalyze();
    await settle(2);
    p.x.switchMarket('us'); // 切换后自动 doAnalyze 默认美股，但这里先清空其 fetch 桩影响
    const areaAfterSwitch = areaHtml(p);
    pending['002436'].resolve(okResult('002436'));
    await first; await settle();
    // 旧请求结果不得写入；页面应是切换后的状态（加载中或新默认分析），不含 002436 结果
    assert.ok(!areaHtml(p).includes('兴森科技'), '切换后旧市场结果不得渲染');
  });

  await test('旧请求结束时不得把仍在分析中的新请求按钮复位', async () => {
    const { p, pending } = racePage();
    setCode(p, '002436'); const first = p.x.doAnalyze();
    p.x.switchMarket('us');
    setCode(p, 'AAPL'); const second = p.x.doAnalyze();
    pending['002436'].resolve(okResult('002436'));
    await first; await settle();
    const btn = p.sandbox.document.getElementById('analyzeBtn');
    assert.strictEqual(btn.disabled, true, '新请求仍在途，按钮应保持禁用');
    pending['AAPL'].resolve(okResult('AAPL'));
    await second; await settle();
    assert.strictEqual(btn.disabled, false, '最新请求完成后按钮恢复');
  });

  console.log(`\n结果: ${passed} 通过, ${failed} 失败`);
  process.exit(failed ? 1 : 0);
})();