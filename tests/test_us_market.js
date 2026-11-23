/**
 * 美股市场支持测试（JS端，与 tests/test_us_market.py 共享 fixture）
 * 1. 从 kline_analyzer.html 提取真实生产函数 detectSplitRatio / adjustUSSplits / isUSCode
 * 2. 用共享期望值验证（tests/us_split_fixtures.json）
 * 3. Cross-Runtime Parity：同一 fixture 分别跑 JS 与 Python，输出必须一致
 * 运行: node tests/test_us_market.js
 */

'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { execFileSync } = require('child_process');
const { extractScriptsFromHtml, createTestSandbox } = require('./fir19_test_helpers');

const ROOT = path.join(__dirname, '..');
const HTML_PATH = path.join(ROOT, 'kline_analyzer.html');

let PASS = 0, FAIL = 0;
function check(name, cond, detail = '') {
  if (cond) { PASS++; }
  else { FAIL++; console.log(`  ✗ ${name} ${detail}`); }
}
function closeEq(a, b, eps = 1e-9) {
  if (a === b) return true;
  const denom = Math.max(Math.abs(a), Math.abs(b), 1e-12);
  return Math.abs(a - b) / denom <= eps;
}

// 加载生产代码
const { sandbox } = createTestSandbox();
const jsCode = extractScriptsFromHtml(HTML_PATH);
try {
  vm.runInContext(jsCode, sandbox);
} catch (e) {
  console.warn('生产代码加载警告（DOM初始化）:', e.message);
}

const detectSplitRatio = sandbox.detectSplitRatio;
const adjustUSSplits = sandbox.adjustUSSplits;
const isUSCode = sandbox.isUSCode;

if (typeof detectSplitRatio !== 'function' || typeof adjustUSSplits !== 'function' || typeof isUSCode !== 'function') {
  console.error('✗ 未能从HTML提取美股数据层函数');
  process.exit(1);
}

const spec = JSON.parse(fs.readFileSync(path.join(__dirname, 'us_split_fixtures.json'), 'utf8'));

// fixture行格式转换：Python用day键，JS用date键
function toJsRows(rows) {
  return rows.map(r => ({ date: r.day, open: r.open, high: r.high, low: r.low, close: r.close, volume: r.volume }));
}

console.log('[1] JS拆股复权共享fixture');
const jsOutputs = {};
for (const fx of spec.fixtures) {
  const out = adjustUSSplits(toJsRows(fx.rows));
  jsOutputs[fx.name] = out;
  check(`${fx.name}: 行数`, out.length === fx.expected.length);
  out.forEach((got, i) => {
    const exp = fx.expected[i];
    for (const k of ['open', 'high', 'low', 'close', 'volume']) {
      check(`${fx.name}[${i}].${k}`, closeEq(got[k], exp[k]), `got=${got[k]} expected=${exp[k]}`);
    }
  });
  const ratios = {};
  for (let i = 1; i < fx.rows.length; i++) {
    const r = detectSplitRatio(fx.rows[i - 1].close, fx.rows[i]);
    if (r) ratios[String(i)] = r;
  }
  const expKeys = Object.keys(fx.expectedRatios);
  check(`${fx.name}: 拆股点`, JSON.stringify(Object.keys(ratios).sort()) === JSON.stringify(expKeys.sort()) &&
    expKeys.every(k => closeEq(ratios[k], fx.expectedRatios[k])),
  `got=${JSON.stringify(ratios)} expected=${JSON.stringify(fx.expectedRatios)}`);
}

console.log('[2] JS detectSplitRatio / isUSCode');
for (const c of spec.detectCases) {
  const r = detectSplitRatio(c.prevClose, { open: c.open, close: c.close });
  if (c.expected === null) check(`detect:${c.name}`, r === null || r === undefined, `got=${r}`);
  else check(`detect:${c.name}`, r != null && closeEq(r, c.expected), `got=${r} expected=${c.expected}`);
}
for (const c of spec.usCodeCases) {
  check(`isUSCode(${JSON.stringify(c.code)})`, isUSCode(c.code) === c.expected);
}

console.log('[3] Cross-Runtime Parity（JS ↔ Python 同一fixture）');
const pyRunner = `
import sys, json
sys.path.insert(0, ${JSON.stringify(path.join(ROOT, 'scripts'))})
from analyze_stock import adjust_us_splits, detect_split_ratio
spec = json.load(sys.stdin)
out = {}
for fx in spec['fixtures']:
    rows = [dict(r) for r in fx['rows']]
    adjusted = adjust_us_splits(rows)
    ratios = {}
    for i in range(1, len(rows)):
        r = detect_split_ratio(rows[i-1]['close'], rows[i])
        if r: ratios[str(i)] = r
    out[fx['name']] = {'adjusted': adjusted, 'ratios': ratios}
print(json.dumps(out))
`;
let pyOutputs;
try {
  const stdout = execFileSync('python3', ['-c', pyRunner], {
    input: JSON.stringify(spec), encoding: 'utf8', maxBuffer: 16 * 1024 * 1024,
  });
  pyOutputs = JSON.parse(stdout);
} catch (e) {
  console.error('✗ Python端运行失败:', e.message);
  process.exit(1);
}

for (const fx of spec.fixtures) {
  const jsOut = jsOutputs[fx.name];
  const pyOut = pyOutputs[fx.name].adjusted;
  check(`parity:${fx.name}: 行数`, jsOut.length === pyOut.length);
  jsOut.forEach((got, i) => {
    for (const k of ['open', 'high', 'low', 'close', 'volume']) {
      check(`parity:${fx.name}[${i}].${k}`, closeEq(got[k], pyOut[i][k]),
        `js=${got[k]} py=${pyOut[i][k]}`);
    }
  });
  const jsRatios = {};
  for (let i = 1; i < fx.rows.length; i++) {
    const r = detectSplitRatio(fx.rows[i - 1].close, fx.rows[i]);
    if (r) jsRatios[String(i)] = r;
  }
  const pyRatios = pyOutputs[fx.name].ratios;
  check(`parity:${fx.name}: 拆股点`, JSON.stringify(jsRatios) === JSON.stringify(pyRatios),
    `js=${JSON.stringify(jsRatios)} py=${JSON.stringify(pyRatios)}`);
}

console.log('='.repeat(60));
console.log(`测试结果: ${PASS} 通过, ${FAIL} 失败`);
console.log('='.repeat(60));
process.exit(FAIL ? 1 : 0);