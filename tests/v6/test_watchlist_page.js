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
function fakeServer({ down = false, holdGets = false } = {}) {
  let t = Date.parse('2026-10-23T08:00:00Z');
  const wl = new W.Watchlist(new W.MemoryWatchlistStore(), () => new Date((t += 1000)));
  const calls = [];
  const held = [];   // holdGets：GET 先在服务端读出快照，响应暂缓，由测试释放（模拟迟到的旧读取）
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
      if (p === '/watchlist' && method === 'GET') {
        const snap = await wl.view();
        if (holdGets) return new Promise(r => held.push(() => r(res(200, snap))));
        return res(200, snap);
      }
      if (p === '/watchlist' && method === 'DELETE') return res(200, await wl.clear());
      if (p === '/watchlist/import' && method === 'POST') return res(201, await wl.importLocal(body));
      if ((m = p.match(/^\/watchlist\/([^/]+)$/)) && method === 'POST') return res(201, await wl.add(m[1]));
      if ((m = p.match(/^\/watchlist\/([^/]+)$/)) && method === 'DELETE') return res(200, await wl.remove(m[1]));
      return res(404, { message: 'no route' });
    } catch (e) {
      return res(e.statusCode || 500, { message: e.message });
    }
  };
  return { wl, fetch, calls, held, set holdGets(v) { holdGets = v; } };
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

  await test('QA R73-1：页面脚本里没有引用未定义的名字（TypeScript 静态检查，含 setTimeout(fn) 这类传引用）', () => {
    const html = fs.readFileSync(PAGE, 'utf8');
    const js = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]).join('\n;\n');
    const file = '/virtual/v4-page.js';
    const host = ts.createCompilerHost({});
    const orig = host.getSourceFile;
    host.getSourceFile = (n, l) => n === file ? ts.createSourceFile(n, js, l, true, ts.ScriptKind.JS) : orig(n, l);
    host.fileExists = n => n === file || ts.sys.fileExists(n);
    host.readFile = n => n === file ? js : ts.sys.readFile(n);
    const prog = ts.createProgram([file], { allowJs: true, checkJs: true, noEmit: true, lib: ['lib.es2022.d.ts', 'lib.dom.d.ts'], types: [] }, host);
    const undef = ts.getPreEmitDiagnostics(prog).filter(d => [2304, 2552, 2582].includes(d.code))
      .map(d => `第 ${d.file.getLineAndCharacterOfPosition(d.start).line + 1} 行：${ts.flattenDiagnosticMessageText(d.messageText, ' ')}`);
    assert.deepStrictEqual(undef, [], undef.join('\n'));
  });

  await test('QA R73-1：真正触发 DOMContentLoaded：不抛错，健康检查与 ?code= 自动分析照常执行', async () => {
    const urls = [];
    const fetch = async (url) => { urls.push(url.replace(/^.*\/api/, '/api')); return { ok: false, status: 503, json: async () => ({ message: 'stub' }), text: async () => 'stub' }; };
    const p = loadPage({ fetch, search: '?code=600519', exports: [] });
    assert.ok(p.listeners.DOMContentLoaded && p.listeners.DOMContentLoaded.length > 0, '页面没有注册 DOMContentLoaded');
    p.fire('DOMContentLoaded');                       // 旧实现在这里抛 ReferenceError: loadGithubSettings is not defined
    for (const fn of p.timeouts.splice(0)) await fn();
    await new Promise(r => setTimeout(r, 20));
    assert.strictEqual(p.sandbox.document.getElementById('codeInput').value, '600519');
    assert.ok(urls.some(u => u.startsWith('/api/health')), `未执行健康检查：${urls}`);
    assert.ok(urls.some(u => u.startsWith('/api/analyze/600519')), `未执行 ?code= 自动分析：${urls}`);
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

  console.log('QA R73-2：迟到的旧读取不能覆盖已完成的更新');
  await test('GET 快照迟到（在 DELETE 之后才返回）：页面保持删除后的结果，rev 不倒退', async () => {
    const server = fakeServer();
    await server.wl.add('600000');
    const p = open(server);
    await until(() => p.state().loaded);
    server.holdGets = true;
    const lateRefresh = p.x.refreshWatchlist(false);           // 切页 / 回到前台触发的刷新：服务端已读出 [600000]
    await until(() => server.held.length === 1);
    await p.x.removeStock('600000');                           // 用户删除，响应先到
    assert.deepStrictEqual(plain(p.state().codes), []);
    const revAfterDelete = p.state().rev;
    server.held.shift()();                                     // 旧快照这时才返回
    await lateRefresh;
    assert.deepStrictEqual(plain(p.state().codes), [], '旧快照把已删除的股票带回了页面');
    assert.strictEqual(p.state().rev, revAfterDelete);
  });

  await test('两个并发刷新 + 添加 + 清空交错：页面最终等于服务端最新状态', async () => {
    const server = fakeServer();
    const p = open(server);
    await until(() => p.state().loaded);
    server.holdGets = true;
    const r1 = p.x.refreshWatchlist(false);
    await until(() => server.held.length === 1);
    p.sandbox.document.getElementById('addStockInput').value = '600519';
    await p.x.addStock();
    const r2 = p.x.refreshWatchlist(false);
    await until(() => server.held.length === 2);
    await p.x.clearAllWatchlist();
    p.sandbox.document.getElementById('addStockInput').value = '002436';
    await p.x.addStock();
    server.held.splice(0).reverse().forEach(f => f());         // 两个旧快照倒序迟到
    await Promise.all([r1, r2]);
    assert.deepStrictEqual(plain(p.state().codes), plain((await server.wl.view()).items));
    assert.deepStrictEqual(plain(p.state().codes), ['002436']);
  });

  await test('另一台设备写入后，本机读取到更大的 rev 会正常更新（不是只认本机请求顺序）', async () => {
    const server = fakeServer();
    const pc = open(server);
    await until(() => pc.state().loaded);
    const before = pc.state().rev;
    await server.wl.add('300750');                             // 手机端直接写服务端
    await pc.x.refreshWatchlist(false);
    assert.deepStrictEqual(plain(pc.state().codes), ['300750']);
    assert.ok(pc.state().rev > before);
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
