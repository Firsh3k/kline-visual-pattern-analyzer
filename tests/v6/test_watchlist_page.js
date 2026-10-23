/**
 * FIR-41 第 2/3 项：实际部署的 V4 页面 + 真实 Watchlist 逻辑（内存存储）端到端。
 * 桩 fetch 把 /api/watchlist* 请求转给编译后的 watchlist-store.ts，模拟服务端；两个页面实例模拟手机 / 电脑。
 *
 * 运行：node tests/v6/test_watchlist_page.js   （需要 typescript 包）
 */
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const ts = require('typescript');
const { loadPage, PAGE } = require('./v4_page_harness');

const ROOT = path.resolve(__dirname, '..', '..');
const SRC = path.join(ROOT, 'v6', 'app-builder', 'server', 'modules', 'stock-analysis', 'watchlist-store.ts');
const out = ts.transpileModule(fs.readFileSync(SRC, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
});
const mod = { exports: {} };
vm.runInThisContext(`(function (module, exports, require) {${out.outputText}\n})`)(mod, mod.exports, require);
const W = mod.exports;

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); passed++; console.log('  ✓ ' + name); }
  catch (e) { failed++; console.log('  ✗ ' + name + '\n      ' + (e && e.stack ? e.stack.split('\n').slice(0, 4).join('\n      ') : e)); }
}
const plain = x => JSON.parse(JSON.stringify(x));   // 页面在 vm 里运行，数组原型不同，比较前转成普通对象
const tick = () => new Promise(r => setImmediate(r));
async function until(cond, ms = 3000) {
  const t0 = Date.now();
  while (!cond()) { if (Date.now() - t0 > ms) throw new Error('等待超时'); await tick(); }
}

/** 模拟服务端：与 controller 相同的路由与错误码 */
function fakeServer({ down = false } = {}) {
  let t = Date.parse('2026-10-23T08:00:00Z');
  const wl = new W.Watchlist(new W.MemoryWatchlistStore(), () => new Date((t += 1000)));
  const calls = [];
  const res = (status, body) => ({ ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body) });
  const fetch = async (url, init = {}) => {
    const method = init.method || 'GET';
    const p = url.replace(/^.*\/api/, '');
    calls.push(`${method} ${p}`);
    if (!p.startsWith('/watchlist')) return res(404, { message: 'not found' });
    if (down) return res(503, { message: '自选股存储不可用：connect ECONNREFUSED' });
    try {
      const body = init.body ? JSON.parse(init.body) : undefined;
      let m;
      if (p === '/watchlist' && method === 'GET') return res(200, await wl.view());
      if (p === '/watchlist' && method === 'DELETE') return res(200, await wl.clear());
      if (p === '/watchlist/import' && method === 'POST') return res(201, await wl.importLocal(body));
      if ((m = p.match(/^\/watchlist\/([^/]+)$/)) && method === 'POST') return res(201, await wl.add(m[1]));
      if ((m = p.match(/^\/watchlist\/([^/]+)$/)) && method === 'DELETE') return res(200, await wl.remove(m[1]));
      return res(404, { message: 'no route' });
    } catch (e) {
      return res(e.statusCode || 500, { message: e.message });
    }
  };
  return { wl, fetch, calls };
}

function open(server, localStorage = {}) {
  const p = loadPage({ fetch: server.fetch, immediateTimers: true, localStorage,
    exports: ['addStock', 'removeStock', 'clearAllWatchlist', 'refreshWatchlist', 'getWatchlist'] });
  p.sandbox.showConfirm = async () => true;               // 页面自定义确认框 → 直接确认
  p.sandbox.analyzeSingleStock = async () => {};          // 添加后自动分析与本测试无关
  p.state = () => vm.runInContext('watchlistState', p.sandbox);
  return p;
}

(async () => {
  console.log('页面源码约束');
  await test('页面不再出现 GitHub 令牌 / GitHub API / 本机写自选股', () => {
    const html = fs.readFileSync(PAGE, 'utf8');
    for (const bad of ['api.github.com', 'ghToken', 'Personal Access Token', 'syncToGithub', 'deleteFromAllDevices', 'saveWatchlist(']) {
      assert.ok(!html.includes(bad), `页面仍包含 ${bad}`);
    }
    assert.ok(!/localStorage\.setItem\(\s*(WATCHLIST_KEY|LEGACY_WATCHLIST_KEY|LEGACY_TOMBSTONES_KEY|'kline_watchlist_v1'|'kline_watchlist_tombstones_v1')/.test(html), '页面仍把自选股写进本机');
  });

  console.log('迁入与令牌清除');
  await test('首次打开：本机旧清单 + 删除记录迁入服务器；GitHub 令牌从浏览器清除；只迁一次', async () => {
    const server = fakeServer();
    const legacy = {
      kline_watchlist_v1: JSON.stringify(['600519', '002436', 'bad']),
      kline_watchlist_tombstones_v1: JSON.stringify([{ code: '300750', deletedAt: '2026-10-01T00:00:00Z' }]),
      kline_github_settings_v1: JSON.stringify({ username: 'Firsh3k', token: 'ghp_SECRET' }),
    };
    const p = open(server, legacy);
    await until(() => p.state().loaded);
    assert.deepStrictEqual(plain(p.state().codes), ['600519', '002436']);
    assert.deepStrictEqual((await server.wl.view()).tombstones.map(t => t.code), ['300750']);
    assert.ok(!('kline_github_settings_v1' in p.storage), '令牌仍留在浏览器');
    assert.ok(!JSON.stringify(p.storage).includes('ghp_SECRET'));
    assert.ok(p.storage.kline_watchlist_migrated_server_v1, '未记录已迁入');
    assert.ok(/已把本机原有自选股迁入服务器/.test(p.sandbox.document.getElementById('syncStatus').textContent));
    // 同一浏览器再次打开：不再迁入
    const p2 = open(server, { ...p.storage });
    await until(() => p2.state().loaded);
    assert.strictEqual(server.calls.filter(c => c === 'POST /watchlist/import').length, 1);
  });

  await test('旧设备迁入不会把已在别处删除的股票带回来', async () => {
    const server = fakeServer();
    await server.wl.add('600519');
    await server.wl.remove('600519');          // 手机上删了
    const pc = open(server, { kline_watchlist_v1: JSON.stringify(['600519', '000858']) });   // 电脑本机旧数据还有
    await until(() => pc.state().loaded);
    assert.deepStrictEqual(plain(pc.state().codes), ['000858']);
  });

  console.log('增删清空与多端同步');
  await test('电脑添加 / 删除，手机刷新后看到同一份；本机不写自选股', async () => {
    const server = fakeServer();
    const pc = open(server), phone = open(server);
    await until(() => pc.state().loaded && phone.state().loaded);
    pc.sandbox.document.getElementById('addStockInput').value = '600519';
    await pc.x.addStock();
    pc.sandbox.document.getElementById('addStockInput').value = '002436';
    await pc.x.addStock();
    await phone.x.refreshWatchlist(true);
    assert.deepStrictEqual(plain(phone.state().codes), ['600519', '002436']);
    await phone.x.removeStock('600519');
    await pc.x.refreshWatchlist(false);
    assert.deepStrictEqual(plain(pc.state().codes), ['002436']);
    assert.ok(!('kline_watchlist_v1' in pc.storage) && !('kline_watchlist_v1' in phone.storage), '自选股被写进了本机');
    assert.ok(/600519/.test(phone.sandbox.document.getElementById('watchlistTags').innerHTML) === false);
  });

  await test('清空全部：服务端一次完成，另一端同步为空', async () => {
    const server = fakeServer();
    await server.wl.add('600519'); await server.wl.add('002436');
    const pc = open(server), phone = open(server);
    await until(() => pc.state().loaded && phone.state().loaded);
    await pc.x.clearAllWatchlist();
    assert.deepStrictEqual(plain(pc.state().codes), []);
    await phone.x.refreshWatchlist(false);
    assert.deepStrictEqual(plain(phone.state().codes), []);
    assert.ok(/已清空 2 只/.test(pc.sandbox.document.getElementById('syncStatus').textContent));
  });

  console.log('服务不可用（fail-closed）');
  await test('服务端 503：明确报错，清单为空，不在本机兜底保存', async () => {
    const server = fakeServer({ down: true });
    const p = open(server, { kline_watchlist_v1: JSON.stringify(['600519']) });
    await until(() => p.state().error);
    assert.deepStrictEqual(plain(p.state().codes), []);
    assert.ok(/自选股服务暂不可用/.test(p.sandbox.document.getElementById('syncStatus').textContent));
    p.sandbox.document.getElementById('addStockInput').value = '002436';
    await p.x.addStock();
    assert.deepStrictEqual(plain(p.state().codes), []);
    assert.ok(/添加失败/.test(p.sandbox.document.getElementById('syncStatus').textContent));
    assert.strictEqual(p.storage.kline_watchlist_v1, JSON.stringify(['600519']), '本机旧数据不应被改动');
    assert.ok(!p.storage.kline_watchlist_migrated_server_v1, '迁入失败时不应标记为已迁入');
  });

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
