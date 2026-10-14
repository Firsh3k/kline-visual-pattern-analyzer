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
const items = ORDER.map((id) => {
  const p = all[id];
  if (!p) throw new Error(`缺少 ${id}`);
  if (/style=/.test(svg[id])) throw new Error(`${id} 示意图含 style 属性（CSP 不允许）`);
  const k = PATTERN_KNOWLEDGE[id] || null;
  return {
    id, name: p.name, direction: p.direction,
    category: id.startsWith('ma_') ? 'trend_state' : 'price_pattern',
    desc: p.desc, features: p.features, volume: p.volume, action: p.action,
    knowledge: k ? { title: k.title, essence: k.essence, causes: k.causes, keyPoints: k.keyPoints,
      strategy: k.strategy, traps: k.traps || null } : null,
    svg: svg[id],
  };
});
const out = {
  schema_version: 1,
  source_file: 'kline_analyzer.html',
  source_sha256: crypto.createHash('sha256').update(fs.readFileSync(SRC)).digest('hex'),
  removed_claims: '未迁移：reliability / reliability_pct / sample_note 中的成功率百分比，以及 PATTERN_ENV_ACCURACY（均未经校准，FIR-13）',
  items,
};
const text = JSON.stringify(out, null, 1) + '\n';
if (process.argv.includes('--check')) {
  process.exit(fs.existsSync(OUT) && fs.readFileSync(OUT, 'utf8') === text ? 0 : 1);
}
fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, text);
console.log(`${items.length} 个形态 → ${path.relative(ROOT, OUT)}`);
