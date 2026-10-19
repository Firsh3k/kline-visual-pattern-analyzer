/**
 * PR #67 QA 阻断项 R67-1 / R67-2 / R67-4 的反例测试——直接跑【实际部署的】V4 页面脚本
 * （v6/app-builder/client/public/v4/index.html），doc 由 tests/v6/r67_make_docs.py 用真实行情 fixture
 * 经 Python core 生成。旧实现（f7e83cf）上这些用例会失败。
 *
 * 运行：node tests/v6/test_v4_adapter_r67.js
 */
'use strict';
const assert = require('assert');
const { execFileSync } = require('child_process');
const path = require('path');
const { loadPage, recordingCanvas, ROOT } = require('./v4_page_harness');

const EXPORTS = ['validatePyDoc', 'pyToV4Match', 'pickDominant', 'buildMatchesFromDoc', 'drawKline',
  'buildChartMarkers', 'renderAnalysis', 'normalizeBatchItem', 'fetchPythonDoc', 'docToV4Data', 'latestInstance'];

const PY = process.env.PYTHON || 'python3';
const DOCS = JSON.parse(execFileSync(PY, [path.join(ROOT, 'tests', 'v6', 'r67_make_docs.py')],
  { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 }));
const clone = o => JSON.parse(JSON.stringify(o));

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); passed++; console.log('  ✓ ' + name); }
  catch (e) { failed++; console.log('  ✗ ' + name + '\n      ' + (e && e.message ? e.message.split('\n').join('\n      ') : e)); }
}

function page(extra = {}) { return loadPage({ exports: EXPORTS, ...extra }); }

/** 用页面自己的 renderAnalysis 渲染一个 doc，返回 resultArea 的 HTML 与页面对象 */
function render(doc, extra = {}) {
  const p = page({ canvasIds: ['chartFull', 'chart60'], ...extra });
  if (extra.beforeRender) extra.beforeRender(p);
  const data = p.x.docToV4Data(doc);
  p.x.renderAnalysis({ name: doc.name, code: doc.code, data }, null, null, { doc });
  return { html: p.sandbox.document.getElementById('resultArea').innerHTML, p };
}

/** 页面 fetchPythonDoc 在桩 fetch 下的返回 */
async function fetchVia(doc) {
  const p = page({ fetch: async () => ({ ok: true, status: 200, json: async () => doc }) });
  return p.x.fetchPythonDoc(doc.code || '002436', null);
}

/** 从 drawKline 的调用记录里取出虚线段（颈线 / 平台线 / 目标 / 止损）与价格→y 映射 */
function dashedSegments(calls) {
  const segs = [];
  for (let i = 0; i < calls.length - 1; i++) {
    const a = calls[i], b = calls[i + 1];
    if (a.op === 'moveTo' && b.op === 'lineTo' && a.dash && a.dash.length) {
      segs.push({ x1: a.args[0], y1: a.args[1], x2: b.args[0], y2: b.args[1] });
    }
  }
  return segs;
}
function priceAxis(calls) {
  // 网格价格标签：fillText(price.toFixed(2), padL-5, y+3)，取前两条求 price→y 仿射映射
  const labels = calls.filter(c => c.op === 'fillText' && /^\d+\.\d{2}$/.test(String(c.args[0])) && c.args[1] === 45).slice(0, 2);
  assert.strictEqual(labels.length, 2, '找不到网格价格标签');
  const [p1, y1] = [Number(labels[0].args[0]), labels[0].args[2] - 3];
  const [p2, y2] = [Number(labels[1].args[0]), labels[1].args[2] - 3];
  return price => y1 + (price - p1) * (y2 - y1) / (p2 - p1);
}
function xAxisFor(startIdx, n) {
  const W = 800, padL = 50, padR = 70, cw = (W - padL - padR) / n;
  return idx => padL + (idx - startIdx) * cw + cw / 2;
}

(async () => {
  console.log('R67-1：确认 / 主导只来自 Python signals');

  await test('QA 复现：W 底 post_break 但缩量 + 回到颈线内侧 2 日 → 页面主导“未确认”', () => {
    const doc = DOCS.w_post_break_unconfirmed;
    assert.strictEqual(doc.signals.latest.w_bottom.confirmed, false);
    const p = page();
    const { matches, bearMatches } = p.x.buildMatchesFromDoc(doc);
    const dom = p.x.pickDominant(doc, matches, bearMatches);
    assert.ok(dom, '应有主导结构');
    assert.strictEqual(dom.id, 'w_bottom');
    assert.strictEqual(dom.confirmed, false, 'pickDominant 不得因阶段名把未确认形态升级为确认');
    assert.ok(!/持股待涨|可加仓/.test(matches.w_bottom.action), '未确认时不得给顺势买入操作文案：' + matches.w_bottom.action);
  });

  await test('真实 002436 快照（同一情形，未改造）渲染：无“已确认买入信号 / ✓ 买入”', () => {
    const { html } = render(DOCS.base_002436);
    assert.ok(!html.includes('已确认买入信号'), '出现了“已确认买入信号”');
    assert.ok(!html.includes('✓ 买入'), '出现了“✓ 买入”');
    assert.ok(html.includes('⏸ 观望'), '应显示观望');
    assert.ok(html.includes('回到颈线内侧'), '应展示 Python 给出的未确认原因');
  });

  await test('Python 判定已确认时页面才显示“已确认买入信号”', () => {
    const doc = DOCS.w_post_break_confirmed;
    assert.strictEqual(doc.signals.dominant.overall.confirmed, true);
    const { html } = render(doc);
    assert.ok(html.includes('已确认买入信号') && html.includes('✓ 买入'));
  });

  await test('个股页正式指标取 core.indicators（不调用前端 calcIndicators）', () => {
    const doc = DOCS.base_002436;
    const { html } = render(doc, { beforeRender: p => { p.sandbox.calcIndicators = () => { throw new Error('在线模式调用了 JS calcIndicators'); }; } });
    assert.ok(html.length > 1000);
  });

  await test('页面忽略被篡改的阶段名：signals 未确认就不显示确认（即使 stage=retest）', () => {
    const doc = clone(DOCS.w_post_break_unconfirmed);
    const s = doc.signals.latest.w_bottom;
    doc.core.patterns.w_bottom[s.instance_index].stage = 'retest';
    s.stage = 'retest';
    const p = page();
    const { matches, bearMatches } = p.x.buildMatchesFromDoc(doc);
    assert.strictEqual(p.x.pickDominant(doc, matches, bearMatches).confirmed, false);
  });

  await test('列表归一化：未确认的活跃形态不进 bull_top；已确认才进', () => {
    const p = page();
    const un = p.x.normalizeBatchItem(DOCS.w_post_break_unconfirmed);
    assert.strictEqual(un.bull_top, null, 'bull_top 应为 null：' + JSON.stringify(un.bull_top));
    const ok = p.x.normalizeBatchItem(DOCS.w_post_break_confirmed);
    assert.ok(ok.bull_top && ok.bull_top.pid === 'w_bottom' && ok.bull_top.confirmed === true);
  });

  await test('列表归一化：缺少信号规则版本 → 按不可用处理，不展示结论', () => {
    const p = page();
    const doc = clone(DOCS.w_post_break_confirmed);
    delete doc.summary.signal_rule_version;
    const it = p.x.normalizeBatchItem(doc);
    assert.strictEqual(it.unavailable, true);
    assert.strictEqual(it.bull_top, null);
  });

  console.log('R67-2：倾斜颈线按 Python 锚点绘制');

  await test('002594 头肩底：线段 (14, 99.14) → (99, 92.765)，经过右颈线点 (50, 96.44)', () => {
    const doc = DOCS.hs_002594;
    const p = page();
    const { matches } = p.x.buildMatchesFromDoc(doc);
    const m = matches.hs_bottom;
    assert.ok(m.__py, '应有头肩底实例');
    const seg = m.markers.segments.find(s => s.label === '颈线');
    assert.ok(seg, 'adapter 必须输出颈线线段（不是单一水平价格）');
    assert.deepStrictEqual([seg.idx1, seg.price1, seg.idx2, seg.price2], [14, 99.14, 99, 92.765]);
    assert.ok(!m.markers.lines.some(l => /颈线/.test(l.label)), '颈线不得再作为水平线输出');
    const rn = m.__py.key_points.find(k => k.name === '右颈线点');
    const atRn = seg.price1 + (seg.price2 - seg.price1) * (rn.index - seg.idx1) / (seg.idx2 - seg.idx1);
    assert.ok(Math.abs(atRn - rn.price) < 0.01, `线段在右颈线点处 ${atRn} ≠ ${rn.price}`);
  });

  await test('drawKline 实际画出倾斜线：两端 y 对应 99.14 / 92.765，中点经过右颈线点', () => {
    const doc = DOCS.hs_002594;
    const p = page();
    const { matches } = p.x.buildMatchesFromDoc(doc);
    const markers = p.x.buildChartMarkers(false, matches.hs_bottom, false, 'bull', null);
    const data = p.x.docToV4Data(doc);
    const cv = recordingCanvas();
    p.x.drawKline(cv, data, null, 0, data.length, false, markers);
    const y = priceAxis(cv.calls), x = xAxisFor(0, data.length);
    const segs = dashedSegments(cv.calls);
    const neck = segs.find(s => Math.abs(s.x1 - x(14)) < 0.01 && Math.abs(s.x2 - x(99)) < 0.01);
    assert.ok(neck, '没有从左颈线点画到最后一根的线段：' + JSON.stringify(segs));
    assert.ok(Math.abs(neck.y1 - y(99.14)) < 0.05 && Math.abs(neck.y2 - y(92.765)) < 0.05, '两端价格不对');
    assert.ok(Math.abs(neck.y1 - neck.y2) > 5, '颈线被画成水平线');
    const yAt50 = neck.y1 + (neck.y2 - neck.y1) * (x(50) - neck.x1) / (neck.x2 - neck.x1);
    assert.ok(Math.abs(yAt50 - y(96.44)) < 0.05, '线段不经过右颈线点');
  });

  await test('近 60 日图：线段按原斜率裁剪到可见区间', () => {
    const doc = DOCS.hs_002594;
    const p = page();
    const { matches } = p.x.buildMatchesFromDoc(doc);
    const markers = p.x.buildChartMarkers(false, matches.hs_bottom, false, 'bull', null);
    const data = p.x.docToV4Data(doc);
    const start = data.length - 60;
    const cv = recordingCanvas();
    p.x.drawKline(cv, data, null, start, data.length, false, markers);
    const y = priceAxis(cv.calls), x = xAxisFor(start, 60);
    const neck = dashedSegments(cv.calls).find(s => Math.abs(s.x1 - x(start)) < 0.01);
    assert.ok(neck, '裁剪后的颈线段缺失');
    const expected = 99.14 + (92.765 - 99.14) * (start - 14) / (99 - 14);
    assert.ok(Math.abs(neck.y1 - y(expected)) < 0.05 && Math.abs(neck.y2 - y(92.765)) < 0.05);
  });

  await test('W 底颈线保持水平（颈线点 → 最后一根）', () => {
    const doc = DOCS.hs_002594;
    const p = page();
    const { matches } = p.x.buildMatchesFromDoc(doc);
    const seg = matches.w_bottom.markers.segments[0];
    const kp = matches.w_bottom.__py.key_points.find(k => k.name === '颈线点');
    assert.strictEqual(seg.idx1, kp.index);
    assert.strictEqual(seg.idx2, doc.bars.length - 1);
    assert.strictEqual(seg.price1, seg.price2);
  });

  console.log('R67-4：未知阶段 / 方向 / 缺字段 fail-closed');

  const mutate = (fn) => { const d = clone(DOCS.base_002436); fn(d, d.signals.latest.w_bottom, d.core.patterns.w_bottom[d.signals.latest.w_bottom.instance_index]); return d; };

  await test('未知阶段 future_new_stage：fetchPythonDoc 拒绝；adapter 抛错而不是显示“构建中”', async () => {
    const doc = mutate((d, s, inst) => { inst.stage = 'future_new_stage'; s.stage = 'future_new_stage'; });
    const r = await fetchVia(doc);
    assert.strictEqual(r.ok, false, 'fetchPythonDoc 不得接受未知阶段');
    assert.ok(/阶段未知|future_new_stage/.test(r.error), r.error);
    const p = page();
    const s = doc.signals.latest.w_bottom;
    assert.throws(() => p.x.pyToV4Match(doc.core.patterns.w_bottom[s.instance_index], s), /未知阶段/);
  });

  await test('未知方向：拒绝，不默认 bull', async () => {
    const doc = mutate((d, s, inst) => { inst.direction = 'sideways'; s.direction = 'sideways'; });
    assert.strictEqual((await fetchVia(doc)).ok, false);
    const p = page();
    const s = doc.signals.latest.w_bottom;
    assert.throws(() => p.x.pyToV4Match(doc.core.patterns.w_bottom[s.instance_index], s), /未知方向/);
  });

  await test('缺分数：拒绝，不默认 0', async () => {
    const doc = mutate((d, s, inst) => { delete inst.score; delete s.score; });
    assert.strictEqual((await fetchVia(doc)).ok, false);
    const p = page();
    const s = doc.signals.latest.w_bottom;
    assert.throws(() => p.x.pyToV4Match(doc.core.patterns.w_bottom[s.instance_index], s), /缺少分数/);
  });

  await test('缺 signals / 规则版本不符 / 颈线线段越界：拒绝', async () => {
    assert.strictEqual((await fetchVia(mutate(d => { delete d.signals; }))).ok, false);
    assert.strictEqual((await fetchVia(mutate(d => { d.signals.rule_version = 'signals-0'; }))).ok, false);
    assert.strictEqual((await fetchVia(mutate((d, s) => { s.lines[0].idx2 = d.bars.length + 5; }))).ok, false);
    assert.strictEqual((await fetchVia(mutate(d => { d.core.indicators.series.rsi = []; }))).ok, false);
  });

  await test('dominant 指向未确认形态却声称已确认：拒绝', async () => {
    assert.strictEqual((await fetchVia(mutate(d => { d.signals.dominant.bull = 'w_bottom'; }))).ok, false);
  });

  await test('合法 doc 通过校验', async () => {
    for (const k of Object.keys(DOCS)) {
      const r = await fetchVia(DOCS[k]);
      assert.strictEqual(r.ok, true, `${k}: ${r.error}`);
    }
  });

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
