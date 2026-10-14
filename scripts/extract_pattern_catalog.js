#!/usr/bin/env node
/**
 * 从 V5 页面 kline_analyzer.html 提取“典型图形库”与“形态知识卡片”文字及示意图 → apps/web/data/pattern_catalog.json
 *
 * FIR-13：旧页面中未经校准的“可靠性 / 成功率 xx%”（reliability_pct、sample_note 中的百分比）
 * 与 PATTERN_ENV_ACCURACY（形态-环境适配度）不迁移；历史统计改由 /api/v1/calibration 提供（不足即 N/A）。
 * 用法：node scripts/extract_pattern_catalog.js [--check]
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const crypto = require('crypto');

const ROOT = path.resolve(__dirname, '..');
const SRC = path.join(ROOT, 'kline_analyzer.html');
const OUT = path.join(ROOT, 'apps/web/data/pattern_catalog.json');
const html = fs.readFileSync(SRC, 'utf8');

function block(startRe, endMarker) {
  const s = html.search(startRe);
  if (s < 0) throw new Error(`找不到 ${startRe}`);
  const e = html.indexOf(endMarker, s);
  return html.slice(s, e + endMarker.length);
}
const code = [
  block(/^const PATTERNS = \[/m, '\n];'),
  block(/^const BEAR_PATTERNS = \[/m, '\n];'),
  block(/^const PATTERN_KNOWLEDGE = \{/m, '\n};'),
  block(/^function getPatternSVG\(id\) \{/m, '\n}'),
  'result = { PATTERNS, BEAR_PATTERNS, PATTERN_KNOWLEDGE, svg: Object.fromEntries([...PATTERNS, ...BEAR_PATTERNS].map(p => [p.id, getPatternSVG(p.id)])) };',
].join('\n');
const ctx = { result: null };
vm.runInNewContext(code, ctx, { timeout: 1000 });
const { PATTERNS, BEAR_PATTERNS, PATTERN_KNOWLEDGE, svg } = ctx.result;

const ORDER = ['w_bottom', 'm_top', 'hs_bottom', 'hs_top', 'platform_break', 'platform_break_down', 'ma_bull', 'ma_bear'];
const all = Object.fromEntries([...PATTERNS.map(p => [p.id, { ...p, direction: 'bull' }]),
  ...BEAR_PATTERNS.map(p => [p.id, { ...p, direction: 'bear' }])]);
// FIR-34 / P6（PO 2026-10-14 采纳）：旧页面中与引擎判定规则矛盾的说法改为引擎口径；每条都必须命中，否则报错
const CORRECTIONS = {
  w_bottom: [
    ['第二个低点不低于第一个低点，', '第二个低点不低于第一个低点的 97%，'],
    ['第二个低点不低于第一个，', '第二个低点不低于第一个的 97%，'],
    ['右底：第二次探底，多头力量增强不再创新低', '右底：第二次探底，不低于左底 3% 以上'],
    ['右底创新低则W底失败。', '右底低于左底 3% 以上则 W 底不成立。'],
    ['假W底：右底创新低，', '假W底：右底低于左底 3% 以上，'],
    ['右底不低于左底', '右底不低于左底 97%'],
  ],
  m_top: [
    ['第二个高点不高于第一个高点，', '第二个高点不高于第一个高点的 103%，'],
    ['第二个高点不高于第一个，', '第二个高点不高于第一个的 103%，'],
    ['右头：第二次冲高，多头力量减弱不再创新高', '右头：第二次冲高，不高于左头 3% 以上'],
    ['右头创新高则M头失败。', '右头高于左头 3% 以上则 M 头不成立。'],
    ['假M头：右头创新高，', '假M头：右头高于左头 3% 以上，'],
    ['右头不高于左头', '右头不高于左头 103%'],
  ],
  hs_bottom: [['（>1.5倍）', '（引擎：近 5 日均量 > 近 20 日均量 1.1 倍）']],
  platform_break: [
    ['（通常20日以上）', '（引擎取最近 5 根之前的 35 根 K 线为平台区间）'],
    ['（量能>5日均量1.5倍）', '（引擎：当日量 > 5 日均量 1.3 倍）'],
    ['振幅<15%为窄幅整理，主力控盘度高；振幅>25%不是平台，是震荡市，假突破多。', '引擎以平台区间振幅 < 18% 视为平台；振幅越小越可靠，振幅过大是震荡市，假突破多。'],
    ['②放量>1.5倍', '②放量（引擎：当日量 > 5 日均量 1.3 倍）'],
  ],
  platform_break_down: [
    ['（通常20日以上）', '（引擎取最近 5 根之前的 35 根 K 线为平台区间）'],
    ['（量能>5日均量1.5倍）', '（引擎：当日量 > 5 日均量 1.3 倍）'],
  ],
};

// 引擎实际判定规则（core/pivot_patterns.py、core/ma_platform_patterns.py；改规则时必须同步）
const ENGINE_RULES = {
  w_bottom: ['两个相邻的已确认摆动低点（左右各 5 根 K 线确认）', '两底差距 ≤ 5%，右底 ≥ 左底 × 0.97', '颈线 = 两底之间最高的摆动高点',
    '突破：收盘 > 颈线 × 1.01，且前一日未越过', '突破后连续 3 日收盘回到颈线下方 = 突破失败', '目标 = 颈线 + (颈线 − 两底最低)；止损 = 右底 × 0.98'],
  m_top: ['两个相邻的已确认摆动高点（左右各 5 根 K 线确认）', '两顶差距 ≤ 5%，右顶 ≤ 左顶 × 1.03', '颈线 = 两顶之间最低的摆动低点',
    '跌破：收盘 < 颈线 × 0.99，且前一日未越过', '跌破后连续 3 日收盘回到颈线上方 = 跌破失败', '目标 = 颈线 − (两顶最高 − 颈线)；止损 = 右顶 × 1.02'],
  hs_bottom: ['三个相邻的已确认摆动低点：头最低，左右肩差距 ≤ 20%', '头相对双肩均价的突出度 ≥ 3%', '颈线连接左右两个反弹高点（可倾斜），外推不超过头部 1.5 倍',
    '右肩低点必须在颈线下方', '突破：收盘 > 颈线 × 1.01，且前一日未越过', '目标 = 颈线 + (颈线 − 头部)；止损 = 头部 × 0.98'],
  hs_top: ['三个相邻的已确认摆动高点：头最高，左右肩差距 ≤ 20%', '头相对双肩均价的突出度 ≥ 3%', '颈线连接左右两个回调低点（可倾斜）',
    '右肩高点必须在颈线上方', '跌破：收盘 < 颈线 × 0.99，且前一日未越过', '目标 = 颈线 − (头部 − 颈线)；止损 = 头部 × 1.02'],
  platform_break: ['平台区间：最近 5 根之前的 35 根 K 线，振幅 < 18%', '突破线 = 平台上沿 × 1.01；当日首次越过 = 突破当日',
    '放量确认：当日量 > 5 日均量 1.3 倍；否则为缩量待确认', '回踩：此前 9 日曾收盘越过上沿 2%，今日回到上沿 ±3% 以内且非首次越过日'],
  platform_break_down: ['平台区间：最近 5 根之前的 35 根 K 线，振幅 < 18%', '跌破线 = 平台下沿 × 0.99；当日首次越过 = 跌破当日',
    '放量确认：当日量 > 5 日均量 1.3 倍；否则为缩量待确认', '回抽：此前 9 日曾收盘跌破下沿 2%，今日回到下沿 ±3% 以内且非首次越过日'],
  ma_bull: ['MA5 > MA10 > MA20 > MA60 为完全多头排列', '收盘在 MA5 上方、MA5 五日斜率向上', '属于“趋势状态”，不是价格形态'],
  ma_bear: ['MA5 < MA10 < MA20 < MA60 为完全空头排列', '收盘在 MA5 下方、MA5 五日斜率向下', '属于“趋势状态”，不是价格形态'],
};

function applyCorrections(id, obj) {
  const rules = CORRECTIONS[id] || [];
  const used = new Set();
  const walk = (v) => {
    if (typeof v === 'string') {
      let out = v;
      rules.forEach(([a, b], i) => { if (out.includes(a)) { out = out.split(a).join(b); used.add(i); } });
      return out;
    }
    if (Array.isArray(v)) return v.map(walk);
    if (v && typeof v === 'object') return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, k === 'svg' ? x : walk(x)]));
    return v;
  };
  const res = walk(obj);
  rules.forEach(([a], i) => { if (!used.has(i)) throw new Error(`${id} 的更正未命中：${a}`); });
  return res;
}

const items = ORDER.map((id) => {
  const p = all[id];
  if (!p) throw new Error(`缺少 ${id}`);
  if (/style=/.test(svg[id])) throw new Error(`${id} 示意图含 style 属性（CSP 不允许）`);
  const k = PATTERN_KNOWLEDGE[id] || null;
  return applyCorrections(id, {
    id, name: p.name, direction: p.direction,
    category: id.startsWith('ma_') ? 'trend_state' : 'price_pattern',
    desc: p.desc, features: p.features, volume: p.volume, action: p.action,
    knowledge: k ? { title: k.title, essence: k.essence, causes: k.causes, keyPoints: k.keyPoints,
      strategy: k.strategy, traps: k.traps || null } : null,
    svg: svg[id],
    engine_rules: ENGINE_RULES[id],
  });
});
const out = {
  schema_version: 1,
  source_file: 'kline_analyzer.html',
  source_sha256: crypto.createHash('sha256').update(fs.readFileSync(SRC)).digest('hex'),
  removed_claims: '未迁移：reliability / reliability_pct / sample_note 中的成功率百分比，以及 PATTERN_ENV_ACCURACY（均未经校准，FIR-13）',
  corrections: CORRECTIONS,
  items,
};
const text = JSON.stringify(out, null, 1) + '\n';
if (process.argv.includes('--check')) {
  process.exit(fs.existsSync(OUT) && fs.readFileSync(OUT, 'utf8') === text ? 0 : 1);
}
fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, text);
console.log(`${items.length} 个形态 → ${path.relative(ROOT, OUT)}`);
