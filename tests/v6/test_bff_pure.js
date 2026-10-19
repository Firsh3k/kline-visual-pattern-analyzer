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
const SRC = path.join(ROOT, 'v6', 'app-builder', 'server', 'modules', 'stock-analysis', 'stock-analysis.pure.ts');
const out = ts.transpileModule(fs.readFileSync(SRC, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, strict: true },
  reportDiagnostics: true,
});
assert.strictEqual((out.diagnostics || []).length, 0, 'TS 编译诊断非空');
const mod = { exports: {} };
vm.runInNewContext(out.outputText, { module: mod, exports: mod.exports, require, Date, JSON, Number, Object, Error });
const { assertAsOf, buildBatchItem, todayShanghai, SIGNAL_RULE_VERSION } = mod.exports;

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
