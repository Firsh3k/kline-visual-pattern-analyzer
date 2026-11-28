/**
 * R67-1 / R67-3：BFF（NestJS）纯函数测试——直接编译部署代码
 * v6/app-builder/server/modules/stock-analysis/stock-analysis.pure.ts（需要 typescript 包，CI 临时安装）。
 *
 * 运行：node tests/v6/test_bff_pure.js
 */
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { execFileSync } = require('child_process');
const ts = require('typescript');

const ROOT = path.resolve(__dirname, '..', '..');
const DIR = path.join(ROOT, 'v6', 'app-builder', 'server', 'modules', 'stock-analysis');
const SRC = path.join(DIR, 'stock-analysis.pure.ts');

function compileTs(file, requireMap) {
  const out = ts.transpileModule(fs.readFileSync(path.join(DIR, file), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, strict: true },
    reportDiagnostics: true,
  });
  assert.strictEqual((out.diagnostics || []).length, 0, `${file} TS 编译诊断非空`);
  const mod = { exports: {} };
  const req = (name) => (name in requireMap ? requireMap[name] : require(name));
  vm.runInThisContext(`(function (module, exports, require) {${out.outputText}\n})`)(mod, mod.exports, req);
  return mod.exports;
}
const dataMod = compileTs('us-exchange-map.data.ts', {});
const pureMod = compileTs('stock-analysis.pure.ts', { './us-exchange-map.data': dataMod });
const {
  assertAsOf, buildBatchItem, todayShanghai, todayNewYork, marketOfCode, SIGNAL_RULE_VERSION,
  normalizeCode, assertCode, assertSupportedCode, isRegisteredCode, chunkCodes,
  InvalidRequestError, UnsupportedCodeError,
} = pureMod;

const DOCS = JSON.parse(execFileSync(process.env.PYTHON || 'python3', [path.join(ROOT, 'tests', 'v6', 'r67_make_docs.py')],
  { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 }));

let passed = 0, failed = 0;
function test(name, fn) {
  try { fn(); passed++; console.log('  ✓ ' + name); }
  catch (e) { failed++; console.log('  ✗ ' + name + '\n      ' + e.message); }
}

// 2026-10-19 12:40 北京时间
const NOON = new Date('2026-10-19T04:40:00Z');
// 2026-10-20 01:00 北京时间（UTC 仍是 10-19）
const AFTER_MIDNIGHT_CN = new Date('2026-10-19T17:00:00Z');

console.log('R67-3：asOf 校验（北京时间）');
test('未来日期拒绝', () => assert.throws(() => assertAsOf('2026-10-20', NOON), /晚于今天/));
test('不存在的日期拒绝', () => assert.throws(() => assertAsOf('2026-02-30', NOON), /不存在/));
test('格式错误拒绝', () => assert.throws(() => assertAsOf('2026/10/19', NOON), /YYYY-MM-DD/));
test('今天 / 过去日期通过；空 = 最新', () => {
  assert.strictEqual(assertAsOf('2026-10-19', NOON), '2026-10-19');
  assert.strictEqual(assertAsOf('2025-01-02', NOON), '2025-01-02');
  assert.strictEqual(assertAsOf(undefined, NOON), undefined);
  assert.strictEqual(assertAsOf('', NOON), undefined);
});
test('按北京时间判断“今天”（UTC 前一天晚上已是北京次日）', () => {
  assert.strictEqual(todayShanghai(AFTER_MIDNIGHT_CN), '2026-10-20');
  assert.strictEqual(assertAsOf('2026-10-20', AFTER_MIDNIGHT_CN), '2026-10-20');
});

console.log('FIR-47：美股 asOf 按美东日期校验（北京已跨日、美东未跨日）');
// AFTER_MIDNIGHT_CN = 2026-10-19T17:00Z = 北京 10-20 01:00 = 美东 10-19 13:00（EDT）
test('美东今天换算正确（含夏令时）', () => {
  assert.strictEqual(todayNewYork(AFTER_MIDNIGHT_CN), '2026-10-19');
  assert.strictEqual(todayShanghai(AFTER_MIDNIGHT_CN), '2026-10-20');
});
test('美股 asOf 美东未来拒绝（北京今天放行）', () => {
  assert.throws(() => assertAsOf('2026-10-20', AFTER_MIDNIGHT_CN, 'us'), /美东时间/);
  assert.strictEqual(assertAsOf('2026-10-20', AFTER_MIDNIGHT_CN, 'cn'), '2026-10-20');
  assert.strictEqual(assertAsOf('2026-10-19', AFTER_MIDNIGHT_CN, 'us'), '2026-10-19');
});
test('marketOfCode 区分美股 / A 股', () => {
  assert.strictEqual(marketOfCode('AAPL'), 'us');
  assert.strictEqual(marketOfCode('BRK.B'), 'us');
  assert.strictEqual(marketOfCode('600519'), 'cn');
  assert.strictEqual(marketOfCode('002436'), 'cn');
});

console.log('FIR-48：双市场代码口径（归一化 / 格式 / 已登记 / 分批）');
test('normalizeCode：去空格、美股转大写，A 股保留前导零；aapl / AAPL / " AAPL " 归一为同一条', () => {
  assert.strictEqual(normalizeCode(' aapl '), 'AAPL');
  assert.strictEqual(normalizeCode('brk.b'), 'BRK.B');
  assert.strictEqual(normalizeCode('002436'), '002436');
  assert.strictEqual(normalizeCode(' 600519 '), '600519');
});
test('assertCode：A 股 6 位数字 / 美股字母（可带 .X）通过；格式错误抛 INVALID_CODE', () => {
  for (const ok of ['600519', '002436', 'AAPL', 'NVDA', 'BRK.B']) assert.strictEqual(assertCode(ok), normalizeCode(ok));
  for (const bad of ['60051', '60000A', '12', '', 'abcdefg', 'BRK.XYZ']) {
    assert.throws(() => assertCode(bad), e => e instanceof InvalidRequestError && e.errorCode === 'INVALID_CODE');
  }
});
test('assertSupportedCode：已登记美股通过；格式合法但未登记抛 UNSUPPORTED_CODE，与格式错误分开', () => {
  assert.strictEqual(assertSupportedCode('BRK.B'), 'BRK.B');
  assert.throws(() => assertSupportedCode('ZZZZ'), e =>
    e instanceof UnsupportedCodeError && e.errorCode === 'UNSUPPORTED_CODE' && /未在当前已登记/.test(e.message));
  assert.throws(() => assertSupportedCode('60051'), e =>
    e instanceof InvalidRequestError && e.errorCode === 'INVALID_CODE');
  assert.strictEqual(isRegisteredCode('600519'), true);
  assert.strictEqual(isRegisteredCode('AAPL'), true);
  assert.strictEqual(isRegisteredCode('ZZZZ'), false);
});
test('chunkCodes：归一化 + 去重保持首次出现顺序；按每批 ≤max 拆分；非法代码抛错', () => {
  const chunks = chunkCodes(['aapl', 'AAPL', ' 600519 ', '600519', 'NVDA'], 2);
  assert.deepStrictEqual(chunks, [['AAPL', '600519'], ['NVDA']]);
  assert.deepStrictEqual(chunkCodes(['BRK.B', 'brk.b'], 200), [['BRK.B']]);
  assert.throws(() => chunkCodes(['60051'], 200), /格式不合法/);
});
test('buildBatchItem：batch item 带 market 字段（cn / us）', () => {
  const cn = buildBatchItem(DOCS.base_002436);
  assert.strictEqual(cn.market, 'cn');
  const usDoc = JSON.parse(JSON.stringify(DOCS.base_002436));
  usDoc.code = 'AAPL';
  usDoc.summary.code = 'AAPL';
  assert.strictEqual(buildBatchItem(usDoc).market, 'us');
});

console.log('R67-1：扫描 / 自选股列表透传 Python 确认');
test('W 底 post_break 未确认 → bull_top 为 null（旧实现按 active+最高分会给出 w_bottom）', () => {
  const it = buildBatchItem(DOCS.w_post_break_unconfirmed);
  assert.strictEqual(it.bull_top, null);
  assert.strictEqual(it.patterns.w_bottom.active, true);
  assert.strictEqual(it.patterns.w_bottom.confirmed, false);
  assert.ok(it.patterns.w_bottom.reasons.length > 0);
});
test('已确认 → bull_top = Python dominant.bull', () => {
  const it = buildBatchItem(DOCS.w_post_break_confirmed);
  assert.deepStrictEqual([it.bull_top.pid, it.bull_top.confirmed], ['w_bottom', true]);
  assert.strictEqual(it.signal_rule_version, SIGNAL_RULE_VERSION);
});
test('真实 002436：与 Python summary.dominant 一致', () => {
  const d = DOCS.base_002436;
  const it = buildBatchItem(d);
  assert.strictEqual(it.bull_top ? it.bull_top.pid : null, d.summary.dominant.bull);
  assert.strictEqual(it.bear_top ? it.bear_top.pid : null, d.summary.dominant.bear);
});
test('缺信号层 / 版本不符 → 抛错（该只计入失败，不降级）', () => {
  const d = JSON.parse(JSON.stringify(DOCS.base_002436));
  d.summary.signal_rule_version = 'signals-0';
  assert.throws(() => buildBatchItem(d), /版本不符/);
  const e = JSON.parse(JSON.stringify(DOCS.base_002436));
  delete e.summary.patterns_latest.w_bottom.confirmed;
  assert.throws(() => buildBatchItem(e), /confirmed/);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
