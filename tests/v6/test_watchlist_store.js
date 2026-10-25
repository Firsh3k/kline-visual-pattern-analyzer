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

  await test('QA R73-2：rev 每次写入严格递增（含迁入“很早时间”的删除记录）；无改动则不变', async () => {
    const { wl } = await fresh();
    const revs = [(await wl.view()).rev];
    revs.push((await wl.add('600519')).rev);
    revs.push((await wl.add('002436')).rev);
    const same = (await wl.add('600519')).rev;            // 已在清单：无写入
    revs.push((await wl.remove('600519')).rev);
    revs.push((await wl.importLocal({ tombstones: [{ code: '300750', deletedAt: '2020-01-01T00:00:00Z' }] })).rev);
    revs.push((await wl.clear()).rev);
    revs.push((await wl.add('600519')).rev);
    assert.strictEqual(revs[0], 0);
    assert.strictEqual(same, revs[2], '无改动的写入不应改变 rev');
    for (let i = 1; i < revs.length; i++) assert.ok(revs[i] > revs[i - 1], `rev 未递增：${revs}`);
    assert.strictEqual(typeof revs[1], 'number');
  });

  await test('上限在写入内检查：满了 400，版本不变；已在清单里的重复 add 不受上限影响', async () => {
    const { store } = await fresh();
    const t = new Date('2026-10-23T08:00:00Z');
    assert.strictEqual(await store.add('600519', t, 1), true);
    const before = (await store.snapshot()).rev;
    await assert.rejects(() => store.add('002436', t, 1), e => e.statusCode === 400 && /最多 1 只/.test(e.message));
    assert.strictEqual(await store.add('600519', t, 1), false);
    const after = await store.snapshot();
    assert.strictEqual(after.rev, before);
    assert.deepStrictEqual(after.rows.filter(r => r.state === 'active').map(r => r.code), ['600519']);
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

/** 外部可控的屏障：wait() 挂起直到 open() */
function barrier() {
  let open;
  const p = new Promise(r => { open = r; });
  return { wait: () => p, open: () => open() };
}
function settled(p) {
  let done = false;
  p.then(() => { done = true; }, () => { done = true; });
  return () => done;
}
async function waitFor(cond, what, ms = 5000) {
  const t0 = Date.now();
  while (!(await cond())) {
    if (Date.now() - t0 > ms) throw new Error('等待超时：' + what);
    await new Promise(r => setTimeout(r, 20));
  }
}

/**
 * FIR-42：模拟妙搭应用账号——对 schema 只有 USAGE、没有 CREATE，只对两张表有 SELECT / INSERT / UPDATE。
 * 表由“平台”（这里是超级用户连接）执行 migration 建好；应用账号跑完整的增删清迁入与并发写。
 */
async function pgLeastPrivilege(url, sql, newTable) {
  const postgres = require('postgres');
  await test('FIR-42：最小权限应用账号（无 CREATE、无 DELETE）——init / 增 / 删 / 清空 / 迁入 / 并发写都正常', async () => {
    const tag = `${process.pid}`;
    const schema = `wl_app_s${tag}`, role = `wl_app_r${tag}`, pw = `pw_${tag}`;
    const table = newTable('lp');
    const u = new URL(url);
    u.username = role; u.password = pw;
    const apps = [];
    try {
      await sql.unsafe(`CREATE SCHEMA ${schema}`);
      await sql.unsafe(`CREATE ROLE ${role} LOGIN PASSWORD '${pw}'`);
      await sql.unsafe(`GRANT USAGE ON SCHEMA ${schema} TO ${role}`);
      const admin = postgres(url, { max: 1, onnotice: () => {}, connection: { search_path: schema } });
      await W.applyWatchlistMigration(admin, table);
      await admin.unsafe(`GRANT SELECT, INSERT, UPDATE ON ${table}, ${table}_meta TO ${role}`);
      await admin.end();

      const mk = () => { const c = postgres(u.toString(), { max: 1, onnotice: () => {}, connection: { search_path: schema } }); apps.push(c); return c; };
      const probe = mk();
      const [priv] = await probe`SELECT has_schema_privilege(${schema}, 'CREATE') AS c, has_table_privilege(${table}, 'DELETE') AS d`;
      assert.deepStrictEqual([priv.c, priv.d], [false, false], '测试账号权限设置不对');
      await assert.rejects(() => probe.unsafe(`CREATE TABLE x_${tag} (a int)`), e => e.code === '42501');

      const wl = new W.Watchlist(new W.PostgresWatchlistStore(mk(), table), clock());
      await wl.init();
      assert.strictEqual(wl.status().ready, true, wl.status().error);
      await wl.add('600519'); await wl.add('002436'); await wl.remove('600519');
      let v = await wl.importLocal({ codes: ['600519', '300750'], tombstones: [{ code: '000858', deletedAt: '2026-10-01T00:00:00Z' }] });
      assert.deepStrictEqual(v.items, ['002436', '300750']);
      assert.strictEqual((await wl.add('002436')).rev, v.rev, '无改动写入回滚后 rev 不变');
      const cleared = await wl.clear();
      assert.strictEqual(cleared.cleared, 2);
      // 两个应用连接并发写：rev 连续、无丢失
      const w1 = new W.Watchlist(new W.PostgresWatchlistStore(mk(), table), clock('2026-10-24T01:00:00Z'));
      const w2 = new W.Watchlist(new W.PostgresWatchlistStore(mk(), table), clock('2026-10-24T02:00:00Z'));
      await w1.init(); await w2.init();
      const before = (await wl.view()).rev;
      await Promise.all([
        (async () => { for (const c of ['600000', '600001', '600002']) await w1.add(c); })(),
        (async () => { for (const c of ['000001', '000002', '000003']) await w2.add(c); })(),
      ]);
      v = await wl.view();
      assert.strictEqual(v.rev, before + 6);
      assert.strictEqual(v.count, 6);
      // 表不存在时的提示（应用账号视角）
      const missing = new W.Watchlist(new W.PostgresWatchlistStore(mk(), `${table}_none`));
      await missing.init();
      assert.ok(/数据表不存在/.test(missing.status().error), missing.status().error);
    } finally {
      for (const c of apps) await c.end();
      await sql.unsafe(`DROP SCHEMA IF EXISTS ${schema} CASCADE`);
      await sql.unsafe(`DROP ROLE IF EXISTS ${role}`);
    }
  });
}

/**
 * QA R73-2 第二轮：两个真实 PG 连接 + 受控提交屏障，复现 QA 给出的顺序：
 *   0) 清单 [600000]
 *   1) 连接 A：迁入 600000 的删除记录，停在提交前
 *   2) 连接 B：add 600001（旧实现：拿到更大的序号并先提交；新实现：等 A 的版本行锁）
 *   3) 第三个连接读取快照 S1
 *   4) 放行 A 提交 → 读取 S2；放行 B 提交 → 读取 S3
 * 要求：不同的已提交状态 rev 不同、按提交顺序递增；页面先收到新快照、再收到迟到的旧快照时不回退；之后的新快照照常接受。
 */
async function pgConcurrency(url, sql, newTable) {
  const postgres = require('postgres');
  const { loadPage } = require('./v4_page_harness');
  const vm = require('vm');

  await test('QA R73-2：两个真实连接并发写（A 迁入删除记录未提交，B 加入）→ 写入按提交顺序串行，版本与数据一一对应', async () => {
    const table = await newTable('cc');
    const connA = postgres(url, { max: 1, onnotice: () => {} });
    const connB = postgres(url, { max: 1, onnotice: () => {} });
    const holdA = barrier(), holdB = barrier();
    const events = [];
    let armed = false;
    const hook = (name, hold) => async (op) => {
      if (!armed) return;
      events.push(`${name}:${op}:ready-to-commit`);
      await hold.wait();
    };
    try {
      const base = new W.PostgresWatchlistStore(sql, table);
      const reader = new W.Watchlist(base, clock('2026-10-23T08:00:00Z'));
      await reader.init();
      const s0 = await reader.add('600000');                       // 08:00:01
      const A = new W.Watchlist(new W.PostgresWatchlistStore(connA, table, { beforeCommit: hook('A', holdA) }), clock('2026-10-23T09:00:00Z'));
      const B = new W.Watchlist(new W.PostgresWatchlistStore(connB, table, { beforeCommit: hook('B', holdB) }), clock('2026-10-23T09:30:00Z'));
      await A.init(); await B.init();
      armed = true;

      const pA = A.importLocal({ tombstones: [{ code: '600000', deletedAt: '2026-10-23T08:30:00Z' }] });
      await waitFor(async () => events.includes('A:import:ready-to-commit'), 'A 停在提交前');
      const pB = B.add('600001');
      const bDone = settled(pB);
      // B 必须在等锁（而不是已经写完）：数据库里能看到一个处于 Lock 等待的会话
      await waitFor(async () => {
        const [{ n }] = await sql`SELECT count(*)::int AS n FROM pg_stat_activity
          WHERE datname = current_database() AND wait_event_type = 'Lock' AND query ILIKE ${'%UPDATE%' + table + '_meta%'}`;
        return n >= 1;
      }, 'B 等待版本行锁');
      assert.strictEqual(bDone(), false, 'A 未提交时 B 不应完成');
      assert.ok(!events.some(e => e.startsWith('B:')), 'A 未提交时 B 不应进入写入');

      const s1 = await reader.view();                              // A 未提交、B 在等
      holdA.open();
      await pA;
      await waitFor(async () => events.includes('B:add:ready-to-commit'), 'B 拿到锁并停在提交前');
      const s2 = await reader.view();                              // 只有 A 已提交
      holdB.open();
      await pB;
      const s3 = await reader.view();                              // A、B 都已提交

      assert.deepStrictEqual(events, ['A:import:ready-to-commit', 'B:add:ready-to-commit'], '提交顺序应为 A → B');
      const states = [s0, s1, s2, s3].map(v => ({ items: v.items, rev: v.rev }));
      assert.deepStrictEqual(states, [
        { items: ['600000'], rev: 1 },
        { items: ['600000'], rev: 1 },          // 未提交的写入不可见，版本也不变
        { items: [], rev: 2 },                  // A 的删除记录
        { items: ['600001'], rev: 3 },          // B 在 A 之后
      ]);
      assert.deepStrictEqual(s3.tombstones.map(t => t.code), ['600000']);

      // 页面规则（真实页面代码）：先收到新快照，再收到迟到的旧快照 → 不回退；之后更新的快照照常接受
      const page = loadPage({ exports: ['_applyWatchlistView'] });
      const apply = page.x._applyWatchlistView;
      const shown = () => JSON.parse(JSON.stringify(vm.runInContext('({codes: watchlistState.codes, rev: watchlistState.rev})', page.sandbox)));
      apply(s3);
      apply(s1);                                // 迟到的旧读取（QA 场景里的 {[600000,600001]} 在新实现下根本不会出现）
      apply(s2);
      assert.deepStrictEqual(shown(), { codes: ['600001'], rev: 3 });
      armed = false;
      const s4 = await B.add('600002');
      apply(s4);
      assert.deepStrictEqual(shown(), { codes: ['600001', '600002'], rev: 4 });
    } finally {
      holdA.open(); holdB.open();
      await connA.end(); await connB.end();
    }
  });

  await test('QA R73-2：三个连接随机并发写 + 并发读取 → 同一 rev 的快照内容完全相同，最终 rev = 有改动的写入次数', async () => {
    const table = await newTable('stress');
    const conns = [0, 1, 2].map(() => postgres(url, { max: 1, onnotice: () => {} }));
    try {
      const wls = conns.map((c, i) => new W.Watchlist(new W.PostgresWatchlistStore(c, table), clock(`2026-10-23T0${i + 1}:00:00Z`)));
      for (const w of wls) await w.init();
      const reader = new W.Watchlist(new W.PostgresWatchlistStore(sql, table));
      const pool = ['600000', '600001', '600002', '600003', '600004', '600005'];
      let seed = 42;
      const rnd = (n) => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed % n; };
      const snaps = [];
      let stop = false;
      const readLoop = (async () => { while (!stop) snaps.push(await reader.view()); })();
      let changes = 0;
      await Promise.all(wls.map(async (w) => {
        for (let k = 0; k < 25; k++) {
          const code = pool[rnd(pool.length)];
          const op = rnd(10);
          if (op < 6) { if ((await w.add(code)).added) changes++; }
          else if (op < 9) { await w.remove(code); changes++; }
          else { if ((await w.clear()).cleared > 0) changes++; }
        }
      }));
      stop = true;
      await readLoop;
      const fin = await reader.view();
      snaps.push(fin);
      assert.strictEqual(fin.rev, changes, `最终 rev=${fin.rev}，有改动的写入=${changes}`);
      const byRev = new Map();
      for (const v of snaps) {
        const key = JSON.stringify([v.items, v.tombstones.map(t => t.code)]);
        if (byRev.has(v.rev)) assert.strictEqual(byRev.get(v.rev), key, `rev=${v.rev} 对应了两种不同内容`);
        else byRev.set(v.rev, key);
      }
      assert.ok(snaps.length > 5, '并发读取次数太少');
    } finally {
      for (const c of conns) await c.end();
    }
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
    assert.strictEqual(wl.status().ready, false, 'QA R73-3：未配置时 ready 必须为 false');
    assert.ok(/SUDA_DATABASE_URL/.test(wl.status().error));
    await assert.rejects(() => wl.view(), e => e.statusCode === 503 && /SUDA_DATABASE_URL/.test(e.message));
    assert.strictEqual((await wl.check()).ready, false);
    assert.strictEqual(W.createWatchlistStore({ WATCHLIST_STORE: 'memory' }).kind, 'memory');
  });
  await test('部署前检查：postgres 驱动缺失 / 连接串错误 → 服务照常启动，仅自选股 503，原因里不含连接串', async () => {
    const url = 'postgres://kline:S3cret@db.internal:5432/app';
    const missing = W.createWatchlistStore({ SUDA_DATABASE_URL: url }, () => { const e = new Error("Cannot find module 'postgres'"); e.code = 'MODULE_NOT_FOUND'; throw e; });
    assert.strictEqual(missing.kind, 'unavailable');
    let wl = new W.Watchlist(missing);
    await wl.init();
    assert.strictEqual(wl.status().ready, false);
    assert.ok(/Cannot find module 'postgres'/.test(wl.status().error), wl.status().error);
    await assert.rejects(() => wl.view(), e => e.statusCode === 503);
    const badUrl = W.createWatchlistStore({ SUDA_DATABASE_URL: url }, () => (u) => { throw new Error('Invalid URL: ' + u); });
    wl = new W.Watchlist(badUrl);
    await wl.init();
    assert.strictEqual(wl.status().ready, false);
    assert.ok(!wl.status().error.includes('S3cret') && !wl.status().error.includes(url), '原因里不能出现连接串：' + wl.status().error);
    // 驱动正常时仍是 PostgreSQL 实现（只构造，不连接）
    const ok = W.createWatchlistStore({ SUDA_DATABASE_URL: url }, () => () => ({}));
    assert.strictEqual(ok.kind, 'postgres');
  });
  await test('数据库建表失败：status 报错，接口 503，恢复后自动重试', async () => {
    let fail = true;
    const inner = new W.MemoryWatchlistStore();
    const store = Object.assign(Object.create(inner), { kind: 'memory', init: async () => { if (fail) throw new Error('permission denied for schema public'); } });
    const wl = new W.Watchlist(store);
    await wl.init();
    const st = wl.status();
    assert.deepStrictEqual([st.kind, st.ready, st.error], ['memory', false, 'permission denied for schema public']);
    await assert.rejects(() => wl.view(), e => e.statusCode === 503 && /permission denied/.test(e.message));
    fail = false;
    assert.deepStrictEqual((await wl.view()).items, []);
    assert.strictEqual(wl.status().ready, true);
  });
  await test('QA R73-3：运行中断连 → 503 且 ready=false；恢复后自动重新就绪', async () => {
    let down = false;
    const inner = new W.MemoryWatchlistStore();
    const guard = (fn) => async (...a) => { if (down) throw Object.assign(new Error('connect ECONNREFUSED 10.0.0.1:5432'), { code: 'ECONNREFUSED' }); return fn.apply(inner, a); };
    const store = { kind: 'postgres', init: guard(inner.init), snapshot: guard(inner.snapshot), add: guard(inner.add),
      remove: guard(inner.remove), clear: guard(inner.clear), importLocal: guard(inner.importLocal) };
    const wl = new W.Watchlist(store);
    await wl.init();
    await wl.add('600519');
    assert.strictEqual((await wl.check()).ready, true);
    down = true;
    await assert.rejects(() => wl.view(), e => e.statusCode === 503 && /ECONNREFUSED/.test(e.message));
    await assert.rejects(() => wl.add('002436'), e => e.statusCode === 503);
    let st = await wl.check();
    assert.deepStrictEqual([st.ready, /ECONNREFUSED/.test(st.error)], [false, true]);
    await assert.rejects(() => wl.add('abc'), e => e.statusCode === 400, '输入错误仍是 400');
    down = false;
    st = await wl.check();
    assert.deepStrictEqual([st.ready, st.error], [true, null]);
    assert.deepStrictEqual((await wl.view()).items, ['600519']);
  });

  console.log('FIR-42：平台适配（静态检查）');
  await test('仓库里的 watchlist.migration.sql 与代码生成的建表脚本一致', () => {
    const file = fs.readFileSync(path.join(path.dirname(SRC), 'watchlist.migration.sql'), 'utf8');
    assert.strictEqual(file, W.watchlistMigrationSql());
    assert.ok(/CREATE TABLE IF NOT EXISTS kline_watchlist \(/.test(file) && /kline_watchlist_meta/.test(file));
  });
  await test('运行时代码不用 advisory lock，不建表（CREATE 只出现在 migration 脚本里）', () => {
    const src = fs.readFileSync(SRC, 'utf8');
    assert.ok(!/pg_advisory/.test(src), '仍在调用 advisory lock');
    const body = src.replace(/export function watchlistMigrationSql[\s\S]*?\n}\n/, '');
    assert.ok(!/CREATE\s+(TABLE|SEQUENCE|INDEX)/i.test(body), '运行时代码里仍有建表语句');
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
        await W.applyWatchlistMigration(sql, table);
        return new W.PostgresWatchlistStore(sql, table);
      });
      await test('FIR-42：运行时不建表——表不存在时 ready=false 并提示执行 migration；migration 可重复执行；代码格式由数据库约束兜底', async () => {
        const table = `kline_watchlist_t${process.pid}_x`;
        tables.push(table);
        const wl = new W.Watchlist(new W.PostgresWatchlistStore(sql, table));
        await wl.init();
        assert.strictEqual(wl.status().ready, false);
        assert.ok(/数据表不存在.*watchlist\.migration\.sql/.test(wl.status().error), wl.status().error);
        const [{ n }] = await sql`SELECT count(*)::int AS n FROM pg_tables WHERE tablename IN (${table}, ${table + '_meta'})`;
        assert.strictEqual(n, 0, '运行时不应建表');
        await W.applyWatchlistMigration(sql, table);
        await W.applyWatchlistMigration(sql, table);
        assert.strictEqual((await wl.check()).ready, true, '执行 migration 后下一次调用应自动恢复');
        const s = new W.PostgresWatchlistStore(sql, table);
        await assert.rejects(() => s.add('abc', new Date()), /check constraint/);
      });
      await test('FIR-42：版本行缺失（平台 migration 只建了表）→ init 用普通 INSERT 补上，rev 接着已有数据', async () => {
        const table = `kline_watchlist_t${process.pid}_seed`;
        tables.push(table);
        await W.applyWatchlistMigration(sql, table);
        const wl = new W.Watchlist(new W.PostgresWatchlistStore(sql, table), clock());
        await wl.init();
        await wl.add('600519'); await wl.add('002436');
        await sql`DELETE FROM ${sql(table + '_meta')}`;
        const wl2 = new W.Watchlist(new W.PostgresWatchlistStore(sql, table), clock('2026-10-24T08:00:00Z'));
        await wl2.init();
        assert.strictEqual(wl2.status().ready, true, wl2.status().error);
        assert.strictEqual((await wl2.view()).rev, 2);
        assert.strictEqual((await wl2.add('300750')).rev, 3);
      });
      await test('FIR-42：平台检查脚本 watchlist-probe.js——建好表时全部通过且不改数据；缺表时 exit 1 并指出', async () => {
        const { execFileSync } = require('child_process');
        const probe = path.join(ROOT, 'v6', 'app-builder', 'scripts', 'watchlist-probe.js');
        const table = `kline_watchlist_t${process.pid}_probe`;
        tables.push(table);
        await W.applyWatchlistMigration(sql, table);
        const wl = new W.Watchlist(new W.PostgresWatchlistStore(sql, table), clock());
        await wl.init(); await wl.add('600519');
        const env = { ...process.env, SUDA_DATABASE_URL: url, WATCHLIST_TABLE: table };
        delete env.WATCHLIST_DATABASE_URL;
        const out = JSON.parse(execFileSync(process.execPath, [probe], { env, encoding: 'utf8' }));
        assert.strictEqual(out.ok, true, JSON.stringify(out.results, null, 1));
        assert.deepStrictEqual(out.results.map(r => r.check), ['驱动', '连接', '表已建好', '应用账号权限', '版本行', '一致读取', '写事务（UPDATE … RETURNING）+ 回滚', '应用写语句（回滚）', '两个连接写入串行']);
        assert.ok(!JSON.stringify(out).includes(url), '输出里不能有连接串');
        const v = await wl.view();
        assert.deepStrictEqual([v.items, v.rev], [['600519'], 1], '检查脚本改动了数据');
        let failedOut;
        try { execFileSync(process.execPath, [probe], { env: { ...env, WATCHLIST_TABLE: `${table}_none` }, encoding: 'utf8' }); }
        catch (e) { failedOut = JSON.parse(e.stdout); assert.strictEqual(e.status, 1); }
        assert.ok(failedOut && failedOut.ok === false, '缺表时应失败');
        assert.strictEqual(failedOut.results.find(r => r.check === '表已建好').ok, false);
      });
      await pgLeastPrivilege(url, sql, (suffix) => { const t = `kline_watchlist_t${process.pid}_${suffix}`; return t; });
      await pgConcurrency(url, sql, async (suffix) => { const t = `kline_watchlist_t${process.pid}_${suffix}`; tables.push(t); await W.applyWatchlistMigration(sql, t); return t; });
    } finally {
      for (const t of tables) await sql`DROP TABLE IF EXISTS ${sql(t)}, ${sql(t + '_meta')}`;
      await sql.end();
    }
  } else {
    console.log('PostgreSQL 实现：跳过（未设置 TEST_DATABASE_URL）');
  }

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
