/**
 * FIR-41 第 4 项（第二部分）：自定义板块存服务端（全站共用，多端同步，删除不复活）。
 * - 存储：同一组用例分别跑内存实现和 PostgreSQL 实现（设置 TEST_DATABASE_URL 时）。
 * - fail-closed：未配置 / 驱动缺失 / 表不存在 → ready=false、503。
 * - 页面：实际部署的 V4 页面，旧浏览器数据一次性迁入、两台设备同步、删除不复活、503 不在本机兜底、迟到旧响应不回退。
 *
 * 运行：node tests/v6/test_custom_sectors.js（需要 typescript；PostgreSQL 用例另需 postgres 包）
 */
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const ts = require('typescript');
const { loadPage } = require('./v4_page_harness');

const ROOT = path.resolve(__dirname, '..', '..');
const DIR = path.join(ROOT, 'v6', 'app-builder', 'server', 'modules', 'stock-analysis');
function compile(file, requireMap) {
  const out = ts.transpileModule(fs.readFileSync(path.join(DIR, file), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, strict: true }, reportDiagnostics: true,
  });
  assert.strictEqual((out.diagnostics || []).length, 0, `${file} TS 诊断非空`);
  const mod = { exports: {} };
  vm.runInThisContext(`(function (module, exports, require) {${out.outputText}\n})`)(mod, mod.exports, (n) => (n in requireMap ? requireMap[n] : require(n)));
  return mod.exports;
}
const WL = compile('watchlist-store.ts', {});
const CS = compile('custom-sector-store.ts', { './watchlist-store': WL });

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
function clock(start = '2026-10-25T08:00:00.000Z') {
  let t = new Date(start).getTime();
  const now = () => new Date((t += 1000));
  now.peek = () => new Date(t);
  return now;
}

async function suite(label, makeStore) {
  console.log(label);
  const fresh = async (now = clock()) => { const c = new CS.CustomSectors(await makeStore(), now); await c.init(); return c; };

  await test('新建 / 相同成分不改动（rev 不变）/ 替换成分（顺序按创建时间不变）', async () => {
    const c = await fresh();
    assert.deepStrictEqual(plain((await c.view()).items), []);
    let v = await c.put('光模块', { codes: ['300308', '300502', '300308'] });
    assert.deepStrictEqual([v.result, v.items[0].codes], ['created', ['300308', '300502']]);
    await c.put('军工', { codes: ['600760'] });
    const rev = (await c.view()).rev;
    v = await c.put('光模块', { codes: ['300308', '300502'] });
    assert.deepStrictEqual([v.result, v.rev], ['unchanged', rev]);
    v = await c.put('光模块', { codes: ['300394'] });
    assert.strictEqual(v.result, 'updated');
    assert.ok(v.rev > rev);
    assert.deepStrictEqual(v.items.map((i) => [i.name, i.codes]), [['光模块', ['300394']], ['军工', ['600760']]]);
  });

  await test('删除留删除记录；重复删除不改动；用户再新建同名板块会重新启用', async () => {
    const c = await fresh();
    await c.put('A', { codes: ['600000'] });
    let v = await c.remove('A');
    assert.deepStrictEqual([v.removed, v.items.length, v.tombstones.map((t) => t.name)], [true, 0, ['A']]);
    const rev = v.rev;
    v = await c.remove('A');
    assert.deepStrictEqual([v.removed, v.rev], [false, rev]);
    v = await c.put('A', { codes: ['600001'] });
    assert.deepStrictEqual([v.result, v.items[0].codes, v.tombstones], ['created', ['600001'], []]);
  });

  await test('迁入：只加入服务器没有记录的板块；在别处删掉的不会被旧浏览器带回来；非法数据跳过', async () => {
    const now = clock();
    const c = await fresh(now);
    await c.put('已有', { codes: ['600000'] });
    await c.put('删了', { codes: ['600001'] });
    await c.remove('删了');
    const v = await c.importLocal({ sectors: { 已有: ['000001'], 删了: ['600001'], 新的: ['300750', 'bad', '300750'], 空的: ['x'], '': ['600000'] } });
    assert.deepStrictEqual([v.added, v.deleted], [1, 0]);
    assert.deepStrictEqual(v.items.map((i) => [i.name, i.codes]), [['已有', ['600000']], ['新的', ['300750']]]);
    assert.deepStrictEqual(v.skipped.sort(), ['', '空的'].sort());
  });

  await test('迁入删除记录：只覆盖比它更早的服务器记录；未来时间按现在处理', async () => {
    const now = clock();
    const c = await fresh(now);
    await c.put('早', { codes: ['600000'] });            // T+1s
    const early = new Date(now.peek().getTime() - 60000).toISOString();
    await c.put('晚', { codes: ['600001'] });            // T+2s
    const late = new Date(now.peek().getTime() + 500).toISOString();
    const v = await c.importLocal({ tombstones: [
      { name: '早', deletedAt: early }, { name: '晚', deletedAt: late }, { name: '未来', deletedAt: '2099-01-01T00:00:00Z' },
    ] });
    assert.deepStrictEqual(v.items.map((i) => i.name), ['早']);
    assert.deepStrictEqual(v.tombstones.map((t) => t.name).sort(), ['晚', '未来'].sort());
    assert.ok(v.tombstones.find((t) => t.name === '未来').deleted_at < '2027-01-01');
  });

  await test('上限与校验：最多 50 个（替换已有不受限）；名称 / 代码非法 → 400', async () => {
    const c = await fresh();
    for (let i = 0; i < CS.MAX_CUSTOM_SECTORS; i++) await c.put(`S${i}`, { codes: ['600000'] });
    await assert.rejects(() => c.put('第51个', { codes: ['600000'] }), (e) => e.statusCode === 400 && /最多 50 个/.test(e.message));
    assert.strictEqual((await c.put('S0', { codes: ['600001'] })).result, 'updated');
    await assert.rejects(() => c.put('', { codes: ['600000'] }), (e) => e.statusCode === 400);
    await assert.rejects(() => c.put('x'.repeat(31), { codes: ['600000'] }), (e) => e.statusCode === 400);
    await assert.rejects(() => c.put('ok', { codes: ['60000'] }), (e) => e.statusCode === 400 && /6 位数字/.test(e.message));
    await assert.rejects(() => c.put('ok', { codes: [] }), (e) => e.statusCode === 400);
    await assert.rejects(() => c.put('ok', { codes: Array.from({ length: 201 }, (_, i) => String(600000 + i)) }), (e) => e.statusCode === 400 && /200/.test(e.message));
  });

  await test('QA R80-1：已有 50 个再迁入 1 个 → 不加入、列在 over_limit、数据与 rev 不变', async () => {
    const c = await fresh();
    for (let i = 0; i < CS.MAX_CUSTOM_SECTORS; i++) await c.put(`S${i}`, { codes: ['600000'] });
    const before = await c.view();
    const v = await c.importLocal({ sectors: { 来自第二台设备: ['600001'] } });
    assert.deepStrictEqual([v.added, v.deleted, plain(v.over_limit)], [0, 0, ['来自第二台设备']]);
    assert.deepStrictEqual([v.count, v.rev], [50, before.rev]);
    assert.ok(!v.items.some((i) => i.name === '来自第二台设备'));
  });

  await test('QA R80-1：先应用迁入里的删除记录再算容量；接近上限的多批迁入在容量内按顺序加入', async () => {
    const now = clock();
    const c = await fresh(now);
    for (let i = 0; i < 48; i++) await c.put(`S${i}`, { codes: ['600000'] });
    let v = await c.importLocal({ sectors: { N1: ['600001'], N2: ['600002'], N3: ['600003'], N4: ['600004'] } });
    assert.deepStrictEqual([v.added, plain(v.over_limit), v.count], [2, ['N3', 'N4'], 50]);
    assert.deepStrictEqual(v.items.slice(-2).map((i) => i.name), ['N1', 'N2']);
    const rev = v.rev;
    v = await c.importLocal({ sectors: { N5: ['600005'] } });
    assert.deepStrictEqual([v.added, plain(v.over_limit), v.count, v.rev], [0, ['N5'], 50, rev]);
    // 同一次迁入里带删除记录：先删掉 1 个，腾出 1 个位置
    v = await c.importLocal({ sectors: { N6: ['600006'], N7: ['600007'] }, tombstones: [{ name: 'S0', deletedAt: new Date(now.peek().getTime() + 500).toISOString() }] });
    assert.deepStrictEqual([v.deleted, v.added, plain(v.over_limit), v.count], [1, 1, ['N7'], 50]);
  });

  await test('rev：每次有改动的写入严格递增，无改动不变', async () => {
    const c = await fresh();
    const revs = [(await c.view()).rev];
    revs.push((await c.put('A', { codes: ['600000'] })).rev);
    const same = (await c.put('A', { codes: ['600000'] })).rev;
    revs.push((await c.remove('A')).rev);
    revs.push((await c.importLocal({ sectors: { B: ['600001'] } })).rev);
    assert.strictEqual(revs[0], 0);
    assert.strictEqual(same, revs[1]);
    for (let i = 1; i < revs.length; i++) assert.ok(revs[i] > revs[i - 1], String(revs));
  });
}

(async () => {
  await suite('内存实现', async () => new CS.MemoryCustomSectorStore());

  console.log('不可用（fail-closed）');
  await test('未配置数据库 / 驱动缺失：ready=false、503，原因里不含连接串', async () => {
    let c = new CS.CustomSectors(CS.createCustomSectorStore({}));
    await c.init();
    assert.strictEqual(c.status().ready, false);
    await assert.rejects(() => c.view(), (e) => e.statusCode === 503 && /SUDA_DATABASE_URL/.test(e.message));
    const url = 'postgres://u:S3cret@h:5432/d';
    c = new CS.CustomSectors(CS.createCustomSectorStore({ SUDA_DATABASE_URL: url }, () => (u) => { throw new Error('Invalid URL: ' + u); }));
    await c.init();
    assert.ok(!c.status().error.includes('S3cret'));
    assert.strictEqual(CS.createCustomSectorStore({ WATCHLIST_STORE: 'memory' }).kind, 'memory');
  });
  await test('建表脚本文件与代码生成的一致；运行时代码不建表、不用 advisory lock', () => {
    const file = fs.readFileSync(path.join(DIR, 'custom-sectors.migration.sql'), 'utf8');
    assert.strictEqual(file, CS.customSectorsMigrationSql());
    const src = fs.readFileSync(path.join(DIR, 'custom-sector-store.ts'), 'utf8');
    assert.ok(!/pg_advisory/.test(src));
    const body = src.replace(/export function customSectorsMigrationSql[\s\S]*?\n}\n/, '');
    assert.ok(!/CREATE\s+(TABLE|SEQUENCE|INDEX)/i.test(body), '运行时代码里有建表语句');
  });

  await test('内存实现中途出错整体回滚：数据与 rev 都不变（与 PostgreSQL 事务一致）', async () => {
    const st = new CS.MemoryCustomSectorStore();
    const t = new Date('2026-10-25T08:00:00Z');
    await st.put('A', ['600000'], t, 50);
    const before = plain(await st.snapshot());
    const bad = { name: 'B', get codes() { throw new Error('boom'); } };
    await assert.rejects(() => st.importLocal([{ name: 'C', codes: ['600001'] }, bad], [{ name: 'A', deletedAt: '2026-10-25T09:00:00Z' }], t, 50), /boom/);
    assert.deepStrictEqual(plain(await st.snapshot()), before);
  });

  const url = process.env.TEST_DATABASE_URL;
  if (url) {
    const postgres = require('postgres');
    const sql = postgres(url, { max: 2, onnotice: () => {} });
    const tables = [];
    let n = 0;
    try {
      await suite('PostgreSQL 实现', async () => {
        const t = `kline_custom_sectors_t${process.pid}_${++n}`;
        tables.push(t);
        await CS.applyCustomSectorsMigration(sql, t);
        return new CS.PostgresCustomSectorStore(sql, t);
      });
      await test('PostgreSQL：表不存在 → ready=false 并提示执行 migration；执行后自动恢复', async () => {
        const t = `kline_custom_sectors_t${process.pid}_x`;
        tables.push(t);
        const c = new CS.CustomSectors(new CS.PostgresCustomSectorStore(sql, t));
        await c.init();
        assert.ok(c.status().ready === false && /custom-sectors\.migration\.sql/.test(c.status().error), c.status().error);
        await CS.applyCustomSectorsMigration(sql, t);
        await CS.applyCustomSectorsMigration(sql, t);
        assert.strictEqual((await c.check()).ready, true);
      });
      await test('QA R80-1：两个连接接近上限时并发迁入，全站在用总数始终 ≤ 50', async () => {
        const t = `kline_custom_sectors_t${process.pid}_lim`;
        tables.push(t);
        await CS.applyCustomSectorsMigration(sql, t);
        const conns = [postgres(url, { max: 1, onnotice: () => {} }), postgres(url, { max: 1, onnotice: () => {} })];
        try {
          const seed = new CS.CustomSectors(new CS.PostgresCustomSectorStore(sql, t), clock());
          await seed.init();
          for (let i = 0; i < 47; i++) await seed.put(`S${i}`, { codes: ['600000'] });
          const cs = conns.map((c, i) => new CS.CustomSectors(new CS.PostgresCustomSectorStore(c, t), clock(`2026-10-25T0${i + 1}:00:00Z`)));
          for (const c of cs) await c.init();
          const rs = await Promise.all(cs.map((c, i) => c.importLocal({ sectors: Object.fromEntries([0, 1, 2, 3].map((k) => [`D${i}-${k}`, [String(600100 + k)]])) })));
          const fin = await seed.view();
          assert.strictEqual(fin.count, 50, `最终 ${fin.count}`);
          assert.strictEqual(rs[0].added + rs[1].added, 3);
          assert.strictEqual(rs[0].over_limit.length + rs[1].over_limit.length, 5);
          const [{ n }] = await sql`SELECT count(*)::int AS n FROM ${sql(t)} WHERE state = 'active'`;
          assert.strictEqual(n, 50);
        } finally { for (const c of conns) await c.end(); }
      });

      await test('PostgreSQL：两个连接并发写，rev 连续不丢、同一 rev 内容一致', async () => {
        const t = `kline_custom_sectors_t${process.pid}_cc`;
        tables.push(t);
        await CS.applyCustomSectorsMigration(sql, t);
        const conns = [postgres(url, { max: 1, onnotice: () => {} }), postgres(url, { max: 1, onnotice: () => {} })];
        try {
          const cs = conns.map((c, i) => new CS.CustomSectors(new CS.PostgresCustomSectorStore(c, t), clock(`2026-10-25T0${i + 1}:00:00Z`)));
          for (const c of cs) await c.init();
          const reader = new CS.CustomSectors(new CS.PostgresCustomSectorStore(sql, t));
          const snaps = [];
          let stop = false;
          const loop = (async () => { while (!stop) snaps.push(await reader.view()); })();
          await Promise.all(cs.map(async (c, i) => { for (let k = 0; k < 10; k++) await c.put(`P${i}-${k % 4}`, { codes: [String(600000 + k)] }); }));
          stop = true;
          await loop;
          const fin = await reader.view();
          assert.strictEqual(fin.rev, 20);
          const byRev = new Map();
          for (const v of [...snaps, fin]) {
            const key = JSON.stringify(v.items.map((i) => [i.name, i.codes]));
            if (byRev.has(v.rev)) assert.strictEqual(byRev.get(v.rev), key, `rev ${v.rev} 对应两种内容`);
            else byRev.set(v.rev, key);
          }
        } finally { for (const c of conns) await c.end(); }
      });
    } finally {
      for (const t of tables) await sql`DROP TABLE IF EXISTS ${sql(t)}, ${sql(t + '_meta')}`;
      await sql.end();
    }
  } else {
    console.log('PostgreSQL 实现：跳过（未设置 TEST_DATABASE_URL）');
  }

  console.log('前端：实际部署的 V4 页面');
  /** 桩服务端：与 controller 相同的路由与错误码，背后是真实 CustomSectors（内存） */
  function server({ down = false, holdGets = false } = {}) {
    let t = Date.parse('2026-10-25T08:00:00Z');
    const cs = new CS.CustomSectors(new CS.MemoryCustomSectorStore(), () => new Date((t += 1000)));
    const calls = [];
    const held = [];
    const res = (status, body) => ({ ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body) });
    const fetch = async (u, init = {}) => {
      const method = init.method || 'GET';
      const p = decodeURIComponent(u.replace(/^.*\/api/, ''));
      calls.push(`${method} ${p}`);
      if (p === '/sectors') return res(200, { today: '2026-10-25', max_scan_codes: 200, sectors: [] });
      if (!p.startsWith('/custom-sectors')) return res(404, { message: 'stub' });
      if (down) return res(503, { message: '自定义板块存储不可用：connect ECONNREFUSED' });
      try {
        const body = init.body ? JSON.parse(init.body) : undefined;
        let m;
        if (p === '/custom-sectors' && method === 'GET') {
          const snap = await cs.view();
          if (holdGets) return new Promise((r) => held.push(() => r(res(200, snap))));
          return res(200, snap);
        }
        if (p === '/custom-sectors/import' && method === 'POST') return res(201, await cs.importLocal(body));
        if ((m = p.match(/^\/custom-sectors\/(.+)$/)) && method === 'PUT') return res(200, await cs.put(m[1], body));
        if ((m = p.match(/^\/custom-sectors\/(.+)$/)) && method === 'DELETE') return res(200, await cs.remove(m[1]));
        return res(404, { message: 'no route' });
      } catch (e) { return res(e.statusCode || 500, { message: e.message }); }
    };
    return { cs, fetch, calls, held, set holdGets(v) { holdGets = v; } };
  }
  function open(srv, localStorage = {}) {
    const p = loadPage({ fetch: srv.fetch, immediateTimers: true, localStorage,
      exports: ['addCustomSector', 'removeCustomSector', 'refreshCustomSectors', 'getCustomSectors', 'exportConfig'] });
    p.alerts = [];
    p.sandbox.alert = (m) => p.alerts.push(String(m));
    p.sandbox.showConfirm = async () => true;
    p.state = () => plain(vm.runInContext('customSectorState', p.sandbox));
    p.chips = () => p.sandbox.document.getElementById('sectorList').innerHTML;
    p.add = async (name, codes) => {
      p.sandbox.document.getElementById('customSectorName').value = name;
      p.sandbox.document.getElementById('customSectorCodes').value = codes;
      await p.x.addCustomSector();
    };
    return p;
  }

  await test('首次打开：本机旧的自定义板块迁入服务器，只迁一次，本机旧数据不改动', async () => {
    const srv = server();
    const legacy = { kline_custom_sectors_v1: JSON.stringify({ 我的: ['600000', '600519'], 坏的: ['x'] }) };
    const p = open(srv, legacy);
    await until(() => p.state().loaded);
    assert.deepStrictEqual(p.state().items, [{ name: '我的', codes: ['600000', '600519'] }]);
    assert.ok(/value="custom:我的"/.test(p.chips()) && /自定义/.test(p.chips()));
    assert.ok(p.storage.kline_custom_sectors_migrated_server_v1, '未记录已迁入');
    assert.strictEqual(p.storage.kline_custom_sectors_v1, legacy.kline_custom_sectors_v1, '本机旧数据不应被改动');
    const p2 = open(srv, { ...p.storage });
    await until(() => p2.state().loaded);
    assert.strictEqual(srv.calls.filter((c) => c === 'POST /custom-sectors/import').length, 1);
  });

  await test('电脑新建 / 删除，手机刷新后看到同一份；旧浏览器迁入不会把删掉的带回来', async () => {
    const srv = server();
    const pc = open(srv, { kline_custom_sectors_migrated_server_v1: 'x' });
    const phone = open(srv, { kline_custom_sectors_migrated_server_v1: 'x' });
    await until(() => pc.state().loaded && phone.state().loaded);
    await pc.add('光通信', '300308, 300502，300394');
    assert.ok(/已添加自定义板块"光通信"（3只股票/.test(pc.alerts.join()), pc.alerts.join());
    await phone.x.refreshCustomSectors();
    assert.deepStrictEqual(phone.state().items, [{ name: '光通信', codes: ['300308', '300502', '300394'] }]);
    await phone.x.removeCustomSector('光通信');
    await pc.x.refreshCustomSectors();
    assert.deepStrictEqual(pc.state().items, []);
    const old = open(srv, { kline_custom_sectors_v1: JSON.stringify({ 光通信: ['300308'] }) });
    await until(() => old.state().loaded);
    assert.deepStrictEqual(old.state().items, [], '在别处删掉的板块被旧浏览器带回来了');
    assert.ok(!('kline_custom_sectors_v1' in pc.storage), '新增的板块不应写进本机');
  });

  await test('同名再保存：替换成分并提示“已更新”', async () => {
    const srv = server();
    const p = open(srv, { kline_custom_sectors_migrated_server_v1: 'x' });
    await until(() => p.state().loaded);
    await p.add('A', '600000');
    await p.add('A', '600001 600002');
    assert.ok(/已更新自定义板块"A"（2只股票）/.test(p.alerts.join()), p.alerts.join());
    assert.deepStrictEqual(p.state().items, [{ name: 'A', codes: ['600001', '600002'] }]);
  });

  await test('服务端 503：明确报错，不在本机兜底保存；新增失败并说明', async () => {
    const srv = server({ down: true });
    const p = open(srv, { kline_custom_sectors_v1: JSON.stringify({ 我的: ['600000'] }) });
    await until(() => p.state().error);
    assert.ok(/自定义板块服务暂不可用：自定义板块存储不可用/.test(p.chips()), p.chips().slice(0, 200));
    assert.ok(!/custom:我的/.test(p.chips()), '不应展示本机旧数据');
    assert.ok(!p.storage.kline_custom_sectors_migrated_server_v1, '迁入失败不应标记为已迁入');
    await p.add('B', '600000');
    assert.ok(/保存自定义板块失败/.test(p.alerts.join()), p.alerts.join());
    assert.strictEqual(p.storage.kline_custom_sectors_v1, JSON.stringify({ 我的: ['600000'] }));
  });

  await test('QA R80-2：名称为 "__proto__" 的板块保存后在页面列表、扫描范围、导出数据里都不丢', async () => {
    const srv = server();
    const p = open(srv, { kline_custom_sectors_migrated_server_v1: 'x' });
    await until(() => p.state().loaded);
    await p.add('__proto__', '600000 600519');
    assert.deepStrictEqual(p.state().items, [{ name: '__proto__', codes: ['600000', '600519'] }]);
    const dict = p.x.getCustomSectors();
    assert.strictEqual(JSON.stringify(dict), '{"__proto__":["600000","600519"]}');
    assert.deepStrictEqual(Object.keys(dict), ['__proto__']);
    assert.ok(/value="custom:__proto__"/.test(p.chips()), '页面列表里应有该板块');
    p.sandbox.document.querySelectorAll = (sel) => (sel === '.sector-checkbox:checked' ? [{ value: 'custom:__proto__' }] : []);
    const scope = plain(vm.runInContext('getScanScope()', p.sandbox));
    assert.deepStrictEqual([scope.customCodes, scope.customNames, scope.total], [['600000', '600519'], ['__proto__'], 2]);
    // 导出文件再导入（JSON 往返）：服务端迁入能识别
    const other = server();
    const v = await other.cs.importLocal({ sectors: JSON.parse(JSON.stringify(dict)) });
    assert.deepStrictEqual(v.items.map((i) => i.name), ['__proto__']);
  });

  await test('QA R80-1：旧浏览器迁入超过全站上限时，页面说明哪些没迁入，本机旧数据不删', async () => {
    const srv = server();
    for (let i = 0; i < 50; i++) await srv.cs.put(`S${i}`, { codes: ['600000'] });
    const legacy = JSON.stringify({ 本机1: ['600001'], 本机2: ['600002'] });
    const p = open(srv, { kline_custom_sectors_v1: legacy });
    await until(() => p.state().loaded && p.state().notice);
    assert.ok(/2 个自定义板块因超过全站 50 个上限未迁入：本机1、本机2/.test(p.chips()), p.chips().slice(0, 300));
    assert.strictEqual(p.storage.kline_custom_sectors_v1, legacy);
  });

  await test('迟到的旧读取不覆盖已完成的更新（rev 不倒退）', async () => {
    const srv = server();
    const p = open(srv, { kline_custom_sectors_migrated_server_v1: 'x' });
    await until(() => p.state().loaded);
    await p.add('A', '600000');
    srv.holdGets = true;
    const late = p.x.refreshCustomSectors();
    await until(() => srv.held.length === 1);
    await p.x.removeCustomSector('A');
    const rev = p.state().rev;
    srv.held.shift()();
    await late;
    assert.deepStrictEqual([p.state().items, p.state().rev], [[], rev]);
  });

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
