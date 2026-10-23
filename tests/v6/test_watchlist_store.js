/**
 * FIR-41 第 2 项：自选股后端存储（全站共用一份清单）。
 * 同一组用例分别跑内存实现和 PostgreSQL 实现（设置 TEST_DATABASE_URL 时；CI 用 postgres 服务容器）。
 *
 * 运行：node tests/v6/test_watchlist_store.js
 *      TEST_DATABASE_URL=postgres://postgres:pw@127.0.0.1:5432/postgres node tests/v6/test_watchlist_store.js
 * 需要 typescript 包；PostgreSQL 用例另需 postgres 包（postgres-js）。
 */
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const ts = require('typescript');

const ROOT = path.resolve(__dirname, '..', '..');
const SRC = path.join(ROOT, 'v6', 'app-builder', 'server', 'modules', 'stock-analysis', 'watchlist-store.ts');
const out = ts.transpileModule(fs.readFileSync(SRC, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, strict: true },
  reportDiagnostics: true,
});
assert.strictEqual((out.diagnostics || []).length, 0, 'TS 诊断非空');
const mod = { exports: {} };
vm.runInThisContext(`(function (module, exports, require) {${out.outputText}\n})`)(mod, mod.exports, require);
const W = mod.exports;

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); passed++; console.log('  ✓ ' + name); }
  catch (e) { failed++; console.log('  ✗ ' + name + '\n      ' + (e && e.stack ? e.stack.split('\n').slice(0, 4).join('\n      ') : e)); }
}

/** 可控时钟：每次调用前进 1 秒 */
function clock(start = '2026-10-23T08:00:00.000Z') {
  let t = new Date(start).getTime();
  const now = () => new Date((t += 1000));
  now.peek = () => new Date(t);
  return now;
}

async function suite(label, makeStore) {
  console.log(label);
  const fresh = async () => {
    const store = await makeStore();
    const wl = new W.Watchlist(store, clock());
    await wl.init();
    return { store, wl };
  };

  await test('空清单；add 新增返回 added=true，重复 add 幂等（added=false，顺序与加入时间不变）', async () => {
    const { wl } = await fresh();
    assert.deepStrictEqual((await wl.view()).items, []);
    assert.strictEqual((await wl.add('600519')).added, true);
    assert.strictEqual((await wl.add('002436')).added, true);
    const again = await wl.add('600519');
    assert.strictEqual(again.added, false);
    assert.deepStrictEqual(again.items, ['600519', '002436']);
    assert.strictEqual(again.count, 2);
    assert.strictEqual(again.storage, (await makeStore()).kind);
  });

  await test('remove 留删除记录；从没加过的代码也写删除记录', async () => {
    const { wl } = await fresh();
    await wl.add('600519'); await wl.add('002436');
    let v = await wl.remove('600519');
    assert.deepStrictEqual(v.items, ['002436']);
    assert.deepStrictEqual(v.tombstones.map(t => t.code), ['600519']);
    v = await wl.remove('300750');
    assert.deepStrictEqual(v.tombstones.map(t => t.code), ['300750', '600519']);
  });

  await test('用户主动加回已删除的股票：清除删除记录，重新排在最后', async () => {
    const { wl } = await fresh();
    await wl.add('600519'); await wl.add('002436'); await wl.remove('600519');
    const v = await wl.add('600519');
    assert.strictEqual(v.added, true);
    assert.deepStrictEqual(v.items, ['002436', '600519']);
    assert.deepStrictEqual(v.tombstones, []);
  });

  await test('clear：全部变删除记录，返回清掉的数量', async () => {
    const { wl } = await fresh();
    await wl.add('600519'); await wl.add('002436');
    const v = await wl.clear();
    assert.strictEqual(v.cleared, 2);
    assert.deepStrictEqual(v.items, []);
    assert.deepStrictEqual(v.tombstones.map(t => t.code), ['002436', '600519']);
    assert.strictEqual((await wl.clear()).cleared, 0);
  });

  await test('迁入：只加入服务器上没有记录的代码；已删除的不会被旧设备带回来', async () => {
    const { wl } = await fresh();
    await wl.add('600519'); await wl.add('002436'); await wl.remove('002436');
    const v = await wl.importLocal({ codes: ['002436', '600519', '300750', '300750'], tombstones: [] });
    assert.deepStrictEqual([v.added, v.deleted], [1, 0]);
    assert.deepStrictEqual(v.items, ['600519', '300750']);
    assert.deepStrictEqual(v.tombstones.map(t => t.code), ['002436']);
  });

  await test('迁入保持本机原有顺序', async () => {
    const { wl } = await fresh();
    const v = await wl.importLocal({ codes: ['600519', '000858', '300750', '002436'] });
    assert.deepStrictEqual(v.items, ['600519', '000858', '300750', '002436']);
  });

  await test('迁入删除记录：只覆盖比它更早的服务器记录（之后在别处加回的保留）', async () => {
    const now = clock();
    const store = await makeStore();
    const wl = new W.Watchlist(store, now);
    await wl.init();
    await wl.add('600519');                   // T+1s
    const early = new Date(now.peek().getTime() - 60000).toISOString();   // 比服务器记录早
    await wl.add('002436');                   // T+2s
    const late = new Date(now.peek().getTime() + 500).toISOString();      // 比服务器上 002436 的记录晚
    const v = await wl.importLocal({ codes: [], tombstones: [
      { code: '600519', deletedAt: early },   // 服务器之后又加过 → 不删
      { code: '002436', deletedAt: late },    // 本机删除更晚 → 删
      { code: '000858', deletedAt: early },   // 服务器无记录 → 写删除记录
    ] });
    assert.deepStrictEqual(v.items, ['600519']);
    assert.deepStrictEqual(v.tombstones.map(t => t.code), ['000858', '002436']);
    assert.strictEqual(v.deleted, 2);
  });

  await test('迁入：非法代码 / 非法日期跳过并列出；未来日期的删除记录按“现在”处理', async () => {
    const { wl } = await fresh();
    const v = await wl.importLocal({ codes: ['60051', 'abcdef', '600519'],
      tombstones: [{ code: '002436', deletedAt: 'not-a-date' }, { code: '300750', deletedAt: '2099-01-01T00:00:00Z' }] });
    assert.deepStrictEqual(v.items, ['600519']);
    assert.deepStrictEqual(v.skipped, ['60051', 'abcdef', '002436']);
    assert.ok(v.tombstones.find(t => t.code === '300750').deleted_at < '2027-01-01', '未来删除时间应被压到现在');
  });

  await test('输入校验：非法代码 400', async () => {
    const { wl } = await fresh();
    await assert.rejects(() => wl.add('60051'), e => e.statusCode === 400);
    await assert.rejects(() => wl.remove('../x'), e => e.statusCode === 400);
  });

  await test('rev 随每次修改单调变化（前端据此判断是否需要刷新）', async () => {
    const { wl } = await fresh();
    const r0 = (await wl.view()).rev;
    const r1 = (await wl.add('600519')).rev;
    const r2 = (await wl.remove('600519')).rev;
    assert.strictEqual(r0, null);
    assert.ok(r1 && r2 && r2 > r1, `${r1} → ${r2}`);
  });

  await test('多端：两个实例（模拟手机 / 电脑）共用同一存储，看到同一份清单', async () => {
    const store = await makeStore();
    const phone = new W.Watchlist(store, clock('2026-10-23T08:00:00Z'));
    const pc = new W.Watchlist(store, clock('2026-10-23T09:00:00Z'));
    await phone.init(); await pc.init();
    await phone.add('600519');
    await pc.add('002436');
    await phone.remove('600519');
    assert.deepStrictEqual((await pc.view()).items, ['002436']);
    // 电脑上的旧浏览器数据迁入时带着 600519，也不会复活
    assert.deepStrictEqual((await pc.importLocal({ codes: ['600519'] })).items, ['002436']);
  });
}

(async () => {
  await suite('内存实现', async () => new W.MemoryWatchlistStore());

  console.log('选择与不可用（fail-closed）');
  await test('未配置数据库：返回 503，不在浏览器本地兜底', async () => {
    const store = W.createWatchlistStore({});
    assert.strictEqual(store.kind, 'unavailable');
    const wl = new W.Watchlist(store);
    await wl.init();
    await assert.rejects(() => wl.view(), e => e.statusCode === 503 && /SUDA_DATABASE_URL/.test(e.message));
    assert.strictEqual(W.createWatchlistStore({ WATCHLIST_STORE: 'memory' }).kind, 'memory');
  });
  await test('数据库建表失败：status 报错，接口 503，恢复后自动重试', async () => {
    let fail = true;
    const inner = new W.MemoryWatchlistStore();
    const store = Object.assign(Object.create(inner), { kind: 'memory', init: async () => { if (fail) throw new Error('permission denied for schema public'); } });
    const wl = new W.Watchlist(store);
    await wl.init();
    assert.deepStrictEqual(wl.status(), { kind: 'memory', ready: false, error: 'permission denied for schema public' });
    await assert.rejects(() => wl.view(), e => e.statusCode === 503 && /permission denied/.test(e.message));
    fail = false;
    assert.deepStrictEqual((await wl.view()).items, []);
    assert.strictEqual(wl.status().ready, true);
  });

  const url = process.env.TEST_DATABASE_URL;
  if (url) {
    const postgres = require('postgres');
    const sql = postgres(url, { max: 2, onnotice: () => {} });
    let n = 0;
    const tables = [];
    try {
      await suite('PostgreSQL 实现', async () => {
        const table = `kline_watchlist_t${process.pid}_${++n}`;
        tables.push(table);
        return new W.PostgresWatchlistStore(sql, table);
      });
      await test('PostgreSQL：建表幂等，代码格式由数据库约束兜底', async () => {
        const table = `kline_watchlist_t${process.pid}_x`;
        tables.push(table);
        const s = new W.PostgresWatchlistStore(sql, table);
        await s.init(); await s.init();
        await assert.rejects(() => s.add('abc', new Date()), /check constraint/);
      });
    } finally {
      for (const t of tables) await sql`DROP TABLE IF EXISTS ${sql(t)}`;
      await sql.end();
    }
  } else {
    console.log('PostgreSQL 实现：跳过（未设置 TEST_DATABASE_URL）');
  }

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
