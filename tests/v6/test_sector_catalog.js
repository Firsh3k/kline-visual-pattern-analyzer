/**
 * FIR-41 第 4 项：板块扫描范围（服务端板块清单 + 来源 / 数据日期 / 核对状态）。
 * - 后端：编译部署的 sector-catalog.ts，用仓库里实际的 catalog.json 与构造的核对场景验证状态规则；
 *   “是否已核对”的新鲜度规则与 Python core.sector_validation 逐日比对（跨运行时）。
 * - 生成：scripts/build_sector_catalog.py --check（catalog.json 与 sector_data 来源一致）。
 * - 前端：加载实际部署的 V4 页面，验证板块从 /api/sectors 读取、状态如实显示、提交只传板块 id、超上限拦截、
 *   清单不可用时不在页面兜底写死成分。
 *
 * 运行：node tests/v6/test_sector_catalog.js（需要 typescript 包、python3）
 */
'use strict';
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const vm = require('vm');
const { execFileSync } = require('child_process');
const ts = require('typescript');
const { loadPage, PAGE } = require('./v4_page_harness');

const ROOT = path.resolve(__dirname, '..', '..');
const DIR = path.join(ROOT, 'v6', 'app-builder', 'server', 'modules', 'stock-analysis');
const CATALOG = path.join(ROOT, 'v6', 'app-builder', 'sector_data', 'catalog.json');
const PY = process.env.PYTHON || 'python3';

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
const DATA = compile('sector-catalog.data.ts', {});
const C = compile('sector-catalog.ts', { './stock-analysis.pure': pure, './sector-catalog.data': DATA });
const { ScanJobManager } = compile('scan-jobs.ts', { './stock-analysis.pure': pure });
const WL = compile('watchlist-store.ts', {});
const CS = compile('custom-sector-store.ts', { './watchlist-store': WL });
/** 服务端自定义板块（内存实现），预置若干板块 */
async function customWith(sectors = {}) {
  const c = new CS.CustomSectors(new CS.MemoryCustomSectorStore());
  await c.init();
  for (const [name, codes] of Object.entries(sectors)) await c.put(name, { codes });
  return c;
}

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); passed++; console.log('  ✓ ' + name); }
  catch (e) { failed++; console.log('  ✗ ' + name + '\n      ' + (e && e.stack ? e.stack.split('\n').slice(0, 4).join('\n      ') : e)); }
}
const plain = (x) => JSON.parse(JSON.stringify(x));
const tick = () => new Promise((r) => setImmediate(r));
async function until(cond, ms = 3000) {
  const t0 = Date.now();
  while (!cond()) { if (Date.now() - t0 > ms) throw new Error('等待超时'); await tick(); }
}

/** 写一个临时清单文件（构造核对场景） */
function tmpCatalog(sectors) {
  const f = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'sector-cat-')), 'catalog.json');
  fs.writeFileSync(f, JSON.stringify({ schema: 'sector-catalog-1', freshness_days: { fresh: 7, aging: 30 }, sectors }));
  return f;
}
const ths = (over) => ({
  id: 'ths:880001', name: '测试板块', kind: 'ths_concept', sector_code: '880001', constituents: ['600000', '600001'],
  source: 'ths_ifind', source_url: null, as_of: '2026-10-20', fetched_at: null, reconciled_at: null,
  reconciliation_method: 'not_reconciled', reconciliation: null, note: null, ...over,
});

(async () => {
  console.log('生成与接入规则');
  await test('catalog.json 与 sector_data 来源一致（scripts/build_sector_catalog.py --check）', () => {
    execFileSync(PY, [path.join(ROOT, 'scripts', 'build_sector_catalog.py'), '--check'], { encoding: 'utf8' });
  });

  const real = JSON.parse(fs.readFileSync(CATALOG, 'utf8'));
  await test('同花顺板块都有来源、数据日期和板块代码；人工样例都没有来源', () => {
    const t = real.sectors.filter((s) => s.kind === 'ths_concept');
    const sm = real.sectors.filter((s) => s.kind === 'curated_sample');
    assert.ok(t.length >= 7 && sm.length >= 1);
    for (const s of t) assert.ok(s.source && /^\d{4}-\d{2}-\d{2}$/.test(s.as_of) && /^\d{6}$/.test(s.sector_code), s.name);
    for (const s of sm) assert.ok(!s.source && !s.as_of && !s.reconciled_at, s.name);
  });

  await test('服务默认读内嵌副本 sector-catalog.data.ts：与 catalog.json 逐字节一致（平台不部署按 cwd 读的 json）', () => {
    const text = fs.readFileSync(CATALOG, 'utf8');
    assert.strictEqual(DATA.SECTOR_CATALOG_JSON, text);
    const sha = require('crypto').createHash('sha256').update(text).digest('hex');
    assert.strictEqual(DATA.SECTOR_CATALOG_SHA256, sha);
    const embedded = new C.SectorCatalogStore();
    assert.deepStrictEqual(plain(embedded.list('2026-10-25')), plain(new C.SectorCatalogStore(CATALOG).list('2026-10-25')));
    assert.deepStrictEqual(plain(embedded.status()), { ready: true, sha256: sha, error: null });
    // 与 cwd 无关：换一个没有 sector_data/ 的工作目录照样可用
    const cwd = process.cwd();
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'catalog-cwd-'));
    try { process.chdir(tmp); assert.strictEqual(new C.SectorCatalogStore().list('2026-10-25').sectors.length, embedded.list('2026-10-25').sectors.length); }
    finally { process.chdir(cwd); fs.rmSync(tmp, { recursive: true, force: true }); }
  });

  await test('内嵌副本被改过（sha256 对不上）→ 板块扫描 503，health 报不可用', () => {
    const bad = new C.SectorCatalogStore({ json: DATA.SECTOR_CATALOG_JSON.replace('CRO', 'CR0'), sha256: DATA.SECTOR_CATALOG_SHA256 });
    assert.throws(() => bad.list('2026-10-25'), (e) => e instanceof C.SectorCatalogUnavailableError && /SECTOR_CATALOG_SHA256/.test(e.message));
    const st = bad.status();
    assert.strictEqual(st.ready, false);
    assert.strictEqual(st.sha256, null);
    assert.ok(/不符/.test(st.error));
  });

  console.log('核对状态（服务端）');
  await test('当前清单：同花顺板块全部“未核对”（从未与权威源对账），样例为“人工样例”，没有一个显示已核对', () => {
    const store = new C.SectorCatalogStore(CATALOG);
    const v = store.list('2026-10-25');
    for (const s of v.sectors) {
      assert.strictEqual(s.verified, false, s.name);
      assert.ok(!/已核对/.test(s.label), `${s.name}: ${s.label}`);
      if (s.kind === 'ths_concept') assert.deepStrictEqual([s.status, s.label], ['unverified', `未核对 · 数据日期 ${s.as_of}`]);
      else assert.deepStrictEqual([s.status, s.label, s.source_label], ['sample', '人工样例 · 无来源', '无来源']);
    }
    const ai = v.sectors.find((s) => s.id === 'ths:886068');
    assert.deepStrictEqual([ai.count, ai.scannable], [1000, false], 'AI视频 1000 只超过单次 200 只上限');
    assert.strictEqual(v.max_scan_codes, 200);
  });

  await test('核对场景：完全一致且 ≤7 天 → 已核对；8–30 天 → 已核对（N 天前）；>30 天 → 已过期；有差异 → 不算已核对', () => {
    const exact = { matched: 2, local_only: 0, source_only: 0 };
    const f = tmpCatalog([
      ths({ id: 'ths:1', reconciled_at: '2026-10-20', reconciliation: exact }),
      ths({ id: 'ths:2', reconciled_at: '2026-10-05', reconciliation: exact }),
      ths({ id: 'ths:3', reconciled_at: '2026-09-20', reconciliation: exact }),
      ths({ id: 'ths:4', reconciled_at: '2026-10-24', reconciliation: { matched: 2, local_only: 1, source_only: 3 } }),
      ths({ id: 'ths:5', reconciled_at: '2026-13-40', reconciliation: exact }),
      ths({ id: 'ths:6', reconciled_at: '2026-10-24', reconciliation: null }),
    ]);
    const v = new C.SectorCatalogStore(f).list('2026-10-25').sectors;
    const got = v.map((s) => [s.id, s.status, s.verified, s.label]);
    assert.deepStrictEqual(got, [
      ['ths:1', 'verified', true, '已核对 · 2026-10-20'],
      ['ths:2', 'verified', true, '已核对 · 2026-10-05（20 天前）'],
      ['ths:3', 'stale', false, '核对已过期（2026-09-20，超过 30 天）'],
      ['ths:4', 'mismatch', false, '核对有差异（本地多 1、权威源多 3）· 2026-10-24'],
      ['ths:5', 'unverified', false, '未核对 · 数据日期 2026-10-20'],
      ['ths:6', 'mismatch', false, '核对结果缺失 · 2026-10-24'],
    ]);
  });

  await test('QA R79-1：核对日期晚于今天（上海）→ 未核对，不显示已核对；今天 / 第 7 天 / 第 8 天 / 第 30 天 / 第 31 天边界正确', async () => {
    const exact = { matched: 2, local_only: 0, source_only: 0 };
    const at = (d, id) => ths({ id, reconciled_at: d, reconciliation: exact });
    const f = tmpCatalog([
      at('2026-10-26', 'tomorrow'), at('2099-01-01', 'far'), at('2026-10-25', 'today'),
      at('2026-10-18', 'd7'), at('2026-10-17', 'd8'), at('2026-09-25', 'd30'), at('2026-09-24', 'd31'),
    ]);
    const v = Object.fromEntries(new C.SectorCatalogStore(f).list('2026-10-25').sectors.map((s) => [s.id, s]));
    for (const id of ['tomorrow', 'far']) {
      assert.deepStrictEqual([v[id].status, v[id].verified, v[id].reconciled_days_ago], ['unverified', false, null], id);
      assert.ok(/晚于今天，视为无效/.test(v[id].label) && !/已核对/.test(v[id].label), v[id].label);
    }
    assert.deepStrictEqual([v.today.status, v.today.label, v.today.reconciled_days_ago], ['verified', '已核对 · 2026-10-25', 0]);
    assert.deepStrictEqual([v.d7.status, v.d7.label], ['verified', '已核对 · 2026-10-18']);
    assert.deepStrictEqual([v.d8.status, v.d8.label], ['verified', '已核对 · 2026-10-17（8 天前）']);
    assert.deepStrictEqual([v.d30.status, v.d30.reconciled_days_ago], ['verified', 30]);
    assert.deepStrictEqual([v.d31.status, v.d31.verified], ['stale', false]);
    // 跨日：上海时间已到 10-26 时，“明天”的核对变成今天，正常显示已核对
    assert.strictEqual(new C.SectorCatalogStore(f).list('2026-10-26').sectors.find((s) => s.id === 'tomorrow').status, 'verified');
  });

  await test('跨运行时：新鲜度与 Python SectorProvenance.get_freshness_level 逐日一致（含未核对 / 非法日期）', () => {
    const today = '2026-10-25';
    const cases = [null, '2026-10-25', '2026-10-18', '2026-10-17', '2026-09-25', '2026-09-24', '2025-01-01', '2026-02-30', 'bad'];
    const py = JSON.parse(execFileSync(PY, ['-c', `
import json, sys
sys.path.insert(0, ${JSON.stringify(ROOT)})
from core.sector_validation.types import SectorProvenance
out = []
for d in json.loads(sys.argv[1]):
    p = SectorProvenance(sector_name='x', authoritative_reconciliation_date=d)
    out.append(p.get_freshness_level(${JSON.stringify(today)}).value)
print(json.dumps(out))`, JSON.stringify(cases)], { encoding: 'utf8' }));
    const js = cases.map((d) => C.freshnessLevel(d, today));
    // Python 对 2026-02-30 这类不存在的日期返回 unknown；JS 的 Date.parse 会把它算成 3 月 2 日，必须同样判 unknown
    assert.deepStrictEqual(js, py, `JS ${js} / Python ${py}`);
  });

  await test('今天按上海时区：UTC 10-24 17:00 已是上海 10-25', () => {
    assert.strictEqual(C.shanghaiToday(new Date('2026-10-24T17:00:00Z')), '2026-10-25');
    assert.strictEqual(C.shanghaiToday(new Date('2026-10-24T15:59:59Z')), '2026-10-24');
  });

  console.log('按板块取成分（服务端）');
  await test('多个板块合并去重保序；扫描范围说明不含成分明细；未知板块 / 非数组 → 400', () => {
    const store = new C.SectorCatalogStore(CATALOG);
    const r = store.resolve(['sample:光模块', 'ths:885927', 'sample:光模块']);
    const a = real.sectors.find((s) => s.id === 'sample:光模块').constituents;
    const b = real.sectors.find((s) => s.id === 'ths:885927').constituents;
    assert.deepStrictEqual(r.codes, [...new Set([...a, ...b])]);
    assert.deepStrictEqual(r.scope.map((s) => s.id), ['sample:光模块', 'ths:885927']);
    assert.ok(r.scope.every((s) => !('codes' in s)), '扫描范围说明不应带成分明细');
    assert.throws(() => store.resolve(['ths:999999']), (e) => e instanceof pure.InvalidRequestError && /未知的板块/.test(e.message));
    assert.throws(() => store.resolve('ths:885927'), (e) => e instanceof pure.InvalidRequestError);
    assert.deepStrictEqual(plain(store.resolve(undefined)), { codes: [], scope: [] });
  });

  await test('清单文件缺失 / 格式不对 → 503（只影响板块扫描）；修好后自动恢复', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'sector-cat-'));
    const f = path.join(dir, 'catalog.json');
    const store = new C.SectorCatalogStore(f);
    assert.throws(() => store.list('2026-10-25'), (e) => e.statusCode === 503 && /板块清单不可用/.test(e.message));
    fs.writeFileSync(f, JSON.stringify({ schema: 'sector-catalog-1', freshness_days: { fresh: 7, aging: 30 }, sectors: [ths({ source: null })] }));
    assert.throws(() => store.list('2026-10-25'), (e) => e.statusCode === 503 && /缺少来源或数据日期/.test(e.message));
    fs.writeFileSync(f, JSON.stringify({ schema: 'sector-catalog-1', freshness_days: { fresh: 7, aging: 30 }, sectors: [ths({})] }));
    assert.strictEqual(store.list('2026-10-25').sectors.length, 1);
  });

  await test('扫描任务带上扫描范围说明并原样回显', () => {
    const m = new ScanJobManager(async (code) => ({ code }), { maxParallel: 1 });
    const scope = [{ id: 'ths:885927', name: 'CRO概念', status: 'unverified', label: '未核对 · 数据日期 2026-10-05', verified: false }];
    const { job_id } = m.create(['600000'], {}, { scope });
    assert.deepStrictEqual(m.get(job_id).scope, scope);
    assert.deepStrictEqual(m.get(m.create(['600001']).job_id).scope, []);
  });

  console.log('前端：实际部署的 V4 页面');
  await test('页面不再写死板块成分（PRESET_SECTORS 已移到服务端清单）', () => {
    const html = fs.readFileSync(PAGE, 'utf8');
    assert.ok(!/PRESET_SECTORS/.test(html), '页面仍有 PRESET_SECTORS');
    assert.ok(!/'光模块'\s*:\s*\[/.test(html) && !/'创新药'\s*:\s*\[/.test(html), '页面仍写死板块成分');
  });

  /** 桩后端：/api/sectors 用真实 SectorCatalogStore；/api/scan/jobs 记录请求体 */
  function backend(store, { sectorsDown = false, custom = null } = {}) {
    const calls = [];
    const res = (status, body) => ({ ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body) });
    const jobs = new Map();
    const fetch = async (url, init = {}) => {
      const method = init.method || 'GET';
      const p = url.replace(/^.*\/api/, '');
      calls.push({ method, p, body: init.body ? JSON.parse(init.body) : undefined });
      if (p === '/sectors') return sectorsDown ? res(503, { message: '板块清单不可用：测试' }) : res(200, plain(store.list('2026-10-25')));
      if (p === '/custom-sectors' && method === 'GET') return custom ? res(200, plain(await custom.view())) : res(503, { message: '未配置' });
      if (p === '/scan/jobs' && method === 'POST') {
        const b = JSON.parse(init.body);
        const r = store.resolve(b.sectors, '2026-10-25');
        const id = 'job-' + (jobs.size + 1);
        const total = new Set([...r.codes, ...(b.codes || [])]).size;
        jobs.set(id, { job_id: id, status: 'done', finished_at: '2026-10-25T00:00:00Z', total, done: total, succeeded: total, matched: 0,
          failed_count: 0, items: [], failed: [], scope: r.scope });
        return res(201, { job_id: id, total });
      }
      const m = p.match(/^\/scan\/jobs\/(.+)$/);
      if (m && jobs.has(m[1])) return res(200, jobs.get(m[1]));
      return res(404, { message: 'stub ' + p });
    };
    return { fetch, calls };
  }
  function open(be, localStorage = {}) {
    const p = loadPage({ fetch: be.fetch, immediateTimers: true, localStorage, exports: ['startMarketScan', 'loadSectors', 'renderSectorList', 'getScanScope'] });
    p.alerts = [];
    p.sandbox.alert = (m) => p.alerts.push(String(m));
    p.state = () => plain(vm.runInContext('({loaded: sectorState.loaded, error: sectorState.error, n: sectorState.sectors.length})', p.sandbox));
    p.check = (values) => { p.sandbox.document.querySelectorAll = (sel) => (sel === '.sector-checkbox:checked' ? values.map((value) => ({ value })) : []); };
    return p;
  }

  await test('打开页面即从 /api/sectors 读取；每个板块显示来源状态：未核对 / 人工样例 / 超上限不可选；当前没有“已核对”', async () => {
    const be = backend(new C.SectorCatalogStore(CATALOG));
    const p = open(be);
    await until(() => p.state().loaded);
    const html = p.sandbox.document.getElementById('sectorList').innerHTML;
    assert.ok(be.calls.some((c) => c.p === '/sectors'));
    assert.ok(/CRO概念[\s\S]*?未核对 · 数据日期 2026-10-05/.test(html), '同花顺板块应显示未核对与数据日期');
    assert.ok(/光模块[\s\S]*?人工样例 · 无来源/.test(html), '样例应标人工样例');
    assert.ok(!/已核对 ·/.test(html), '当前清单不应出现“已核对”');
    assert.ok(/value="ths:886068" class="sector-checkbox" disabled/.test(html) && /超过 200 只上限/.test(html), 'AI视频 1000 只应不可选并说明原因');
  });

  await test('服务端判定已核对的板块才显示“已核对”', async () => {
    const f = tmpCatalog([ths({ reconciled_at: '2026-10-24', reconciliation: { matched: 2, local_only: 0, source_only: 0 } })]);
    const p = open(backend(new C.SectorCatalogStore(f)));
    await until(() => p.state().loaded);
    assert.ok(/data-status="verified"[^>]*>已核对 · 2026-10-24</.test(p.sandbox.document.getElementById('sectorList').innerHTML));
  });

  await test('提交扫描：板块只传 id（服务端取成分），自定义板块传代码；结果显示扫描范围与核对状态提示', async () => {
    const be = backend(new C.SectorCatalogStore(CATALOG), { custom: await customWith({ 我的: ['600000', '600519'] }) });
    const p = open(be);
    await until(() => p.state().loaded && /custom:我的/.test(p.sandbox.document.getElementById('sectorList').innerHTML));
    p.check(['ths:885927', 'sample:光模块', 'custom:我的']);
    const doc = p.sandbox.document;
    doc.getElementById('scanMinScore').value = '0';
    doc.getElementById('scanSignalType').value = 'all';
    await p.x.startMarketScan();
    const post = be.calls.find((c) => c.method === 'POST' && c.p === '/scan/jobs');
    assert.deepStrictEqual(post.body.sectors, ['ths:885927', 'sample:光模块']);
    assert.deepStrictEqual(post.body.codes, ['600000', '600519']);
    const status = doc.getElementById('scanStatus').innerHTML;
    assert.ok(/扫描范围：CRO概念（.*未核对 · 数据日期 2026-10-05.*）、光模块（.*人工样例 · 无来源.*）、我的（.*自定义.*）/.test(status), status);
    assert.ok(/范围里有未核对 \/ 人工样例的板块/.test(status));
  });

  await test('合并后超过 200 只：页面直接拦截并说明，不提交', async () => {
    const be = backend(new C.SectorCatalogStore(CATALOG));
    const p = open(be);
    await until(() => p.state().loaded);
    p.check(['ths:886015', 'ths:885927', 'ths:885769', 'ths:885959']);   // 106 + 73 + 62 + 45，去重后仍超过 200
    const scope = plain(p.x.getScanScope());
    assert.ok(scope.total > 200, `合并后 ${scope.total}`);
    await p.x.startMarketScan();
    assert.ok(!be.calls.some((c) => c.method === 'POST'), '超上限不应提交');
    assert.ok(/超过单次扫描上限 200 只/.test(p.alerts.join('\n')), p.alerts.join('\n'));
  });

  await test('板块清单不可用（503）：明确报错，只保留自定义板块，不在页面兜底写死成分', async () => {
    const be = backend(new C.SectorCatalogStore(CATALOG), { sectorsDown: true, custom: await customWith({ 我的: ['600000'] }) });
    const p = open(be);
    await until(() => p.state().error && /custom:我的/.test(p.sandbox.document.getElementById('sectorList').innerHTML));
    const html = p.sandbox.document.getElementById('sectorList').innerHTML;
    assert.ok(/板块清单加载失败：板块清单不可用：测试/.test(html), html.slice(0, 200));
    assert.ok(!/CRO概念|光模块/.test(html), '不可用时不应出现任何内置板块');
    assert.ok(/value="custom:我的"/.test(html), '自定义板块仍可用');
  });

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
