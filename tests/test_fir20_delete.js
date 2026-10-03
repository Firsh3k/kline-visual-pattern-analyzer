/**
 * FIR-20 删除后股票被同步复活 回归测试
 * 使用从 kline_analyzer.html 提取的真实生产函数
 *
 * 运行方式: node tests/test_fir20_delete.js
 */

const { createTestSandbox, loadProductionCode } = require('./fir19_test_helpers');

// ========== 测试框架 ==========
let passed = 0;
let failed = 0;
const failures = [];

function assert(condition, message) {
  if (condition) { passed++; console.log(`  ✅ ${message}`); }
  else { failed++; failures.push(message); console.error(`  ❌ ${message}`); }
}

function assertEqual(actual, expected, message) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a === e) { passed++; console.log(`  ✅ ${message}`); }
  else { failed++; failures.push(`${message}\n     期望: ${e}\n     实际: ${a}`); console.error(`  ❌ ${message}\n     期望: ${e}\n     实际: ${a}`); }
}

// ========== 初始化 ==========
console.log('\n' + '='.repeat(60));
console.log('FIR-20 删除后股票被同步复活 回归测试（生产代码路径）');
console.log('='.repeat(60));

const env = createTestSandbox();
const { callHistory, setMockFetch, resetMocks, getLocalStorage, setLocalStorage, runInSandbox } = env;
const prod = loadProductionCode(env.sandbox);

assert(typeof prod.syncToGithub === 'function', '生产函数 syncToGithub 已加载');
assert(typeof prod.mergeWatchlistsWithTombstones === 'function', '生产函数 mergeWatchlistsWithTombstones 已加载');

// ========== 异步测试主函数 ==========
async function runTests() {

  // 场景A: 普通删除创建tombstone
  console.log('\n【场景A】普通删除 → 创建tombstone + 从本地移除');
  {
    resetMocks();
    setLocalStorage({ 'kline_watchlist_v1': JSON.stringify(['A1','A2','A3']) });
    env.sandbox.confirm = () => true;

    runInSandbox('removeStock("A2")');

    const tombstones = JSON.parse(getLocalStorage()['kline_watchlist_tombstones_v1'] || '[]');
    assertEqual(tombstones.length, 1, '应创建1条tombstone');
    assertEqual(tombstones[0].code, 'A2', 'tombstone code应为A2');
    assert(tombstones[0].deletedAt, 'tombstone应包含deletedAt');

    const watchlist = JSON.parse(getLocalStorage()['kline_watchlist_v1']);
    assertEqual(watchlist, ['A1','A3'], 'A2应从本地watchlist移除');
  }

  // 场景B: 删除后同步 → tombstone阻止远程复活
  console.log('\n【场景B】删除后同步 → tombstone阻止远程复活（QA场景A+B）');
  {
    resetMocks();
    setLocalStorage({ 'kline_watchlist_v1': JSON.stringify(['A1','A2','A3']) });
    env.sandbox.confirm = () => true;

    // 先删除A2（创建tombstone）
    runInSandbox('removeStock("A2")');

    // mock fetch：远程有[A1,A2,A3]（A2还在远程）
    let putCount = 0, putBody = null;
    setMockFetch(async (url, options) => {
      if (options?.method === 'PUT') {
        putCount++;
        putBody = JSON.parse(options.body);
        return { ok: true, status: 200, json: async () => ({}) };
      }
      return {
        ok: true, status: 200,
        json: async () => ({
          sha: 'remote123',
          content: Buffer.from(JSON.stringify({ watchlist: ['A1','A2','A3'] })).toString('base64'),
        }),
      };
    });

    // 等待异步同步完成
    await runInSandbox('syncToGithub()');

    assert(putBody !== null, '应执行PUT操作');
    const decoded = JSON.parse(Buffer.from(putBody.content, 'base64').toString('utf8'));
    assert(!decoded.watchlist.includes('A2'), '同步后A2不应出现在watchlist中（tombstone阻止复活）');
    assert(decoded.watchlist.includes('A1') && decoded.watchlist.includes('A3'), 'A1和A3应保留');
    assert(decoded.tombstones && decoded.tombstones.find(t => t.code === 'A2'), 'tombstone应同步到远程');

    const localWatchlist = JSON.parse(getLocalStorage()['kline_watchlist_v1']);
    assert(!localWatchlist.includes('A2'), '本地watchlist不应包含A2');

    console.log(`  📊 远程有A2，同步后A2被tombstone过滤: ${!decoded.watchlist.includes('A2')}`);
  }

  // 场景C: 过期第二设备同步 → 不复活
  console.log('\n【场景C】过期第二设备同步 → 已删除股票不复活（QA场景B）');
  {
    resetMocks();
    setLocalStorage({ 'kline_watchlist_v1': JSON.stringify(['A1','A2','A3']) });

    let putCount = 0, putBody = null;
    setMockFetch(async (url, options) => {
      if (options?.method === 'PUT') {
        putCount++;
        putBody = JSON.parse(options.body);
        return { ok: true, status: 200, json: async () => ({}) };
      }
      return {
        ok: true, status: 200,
        json: async () => ({
          sha: 'remote456',
          content: Buffer.from(JSON.stringify({
            watchlist: ['A1','A3'],
            tombstones: [{ code: 'A2', deletedAt: '2026-10-03T10:00:00.000Z' }],
          })).toString('base64'),
        }),
      };
    });

    await runInSandbox('syncToGithub()');

    const localWatchlist = JSON.parse(getLocalStorage()['kline_watchlist_v1']);
    assert(!localWatchlist.includes('A2'), '过期设备同步后A2不应复活');
    assertEqual(localWatchlist, ['A1','A3'], '本地应为[A1,A3]');

    assert(putBody !== null, '应执行PUT操作');
    const decoded = JSON.parse(Buffer.from(putBody.content, 'base64').toString('utf8'));
    assert(!decoded.watchlist.includes('A2'), '远程也不应包含A2');

    console.log(`  📊 过期设备有A2，同步后A2被远程tombstone过滤: ${!localWatchlist.includes('A2')}`);
  }

  // 场景D: 全局清空（远程404首次创建）→ 原子事务PUT成功
  console.log('\n【场景D】全局清空（远程404）→ 原子事务PUT，本地清空');
  {
    resetMocks();
    setLocalStorage({ 'kline_watchlist_v1': JSON.stringify(['A1','A2','A3']) });
    env.sandbox.confirm = () => true;

    let putCount = 0, putBody = null;
    setMockFetch(async (url, options) => {
      if (options?.method === 'PUT') {
        putCount++;
        putBody = JSON.parse(options.body);
        return { ok: true, status: 201, json: async () => ({}) };
      }
      return { ok: false, status: 404, json: async () => ({ message: 'Not Found' }) };
    });

    await runInSandbox('clearAllWatchlist()');

    assertEqual(putCount, 1, '应执行1次PUT（原子事务）');
    const tombstones = JSON.parse(getLocalStorage()['kline_watchlist_tombstones_v1'] || '[]');
    assertEqual(tombstones.length, 3, '应为3只股票创建tombstone');
    const watchlist = JSON.parse(getLocalStorage()['kline_watchlist_v1']);
    assertEqual(watchlist, [], '本地watchlist应为空（PUT成功后才清空）');

    const decoded = JSON.parse(Buffer.from(putBody.content, 'base64').toString('utf8'));
    assertEqual(decoded.watchlist, [], 'PUT内容watchlist应为空');
    assertEqual(decoded.tombstones.length, 3, 'PUT内容应包含3条tombstone');
  }

  // 场景E: 取消清空 → 不做任何操作（股票保留）
  console.log('\n【场景E】取消清空 → 中止操作，股票保留（Blocker 3修复）');
  {
    resetMocks();
    setLocalStorage({ 'kline_watchlist_v1': JSON.stringify(['B1','B2','B3']) });
    env.sandbox.confirm = () => false;

    await runInSandbox('clearAllWatchlist()');

    const tombstones = JSON.parse(getLocalStorage()['kline_watchlist_tombstones_v1'] || '[]');
    assertEqual(tombstones.length, 0, '取消清空不应创建tombstone');
    const watchlist = JSON.parse(getLocalStorage()['kline_watchlist_v1']);
    assertEqual(watchlist, ['B1','B2','B3'], '取消清空后股票应保留');
  }

  // 场景F: 取消删除（单只）→ 不创建tombstone，股票保留
  console.log('\n【场景F】取消删除（单只）→ 不创建tombstone，股票保留');
  {
    resetMocks();
    setLocalStorage({ 'kline_watchlist_v1': JSON.stringify(['C1','C2','C3']) });
    env.sandbox.confirm = () => false;

    runInSandbox('removeStock("C2")');

    const tombstones = JSON.parse(getLocalStorage()['kline_watchlist_tombstones_v1'] || '[]');
    assertEqual(tombstones.length, 0, '取消删除不应创建tombstone');
    const watchlist = JSON.parse(getLocalStorage()['kline_watchlist_v1']);
    assertEqual(watchlist, ['C1','C2','C3'], '取消删除后股票应保留');
  }

  // 场景G: 全局清空（远程有独有股票）→ 为remote ∪ local创建tombstone
  console.log('\n【场景G】全局清空（远程有独有股票）→ 为remote∪local创建tombstone（Blocker 2）');
  {
    resetMocks();
    setLocalStorage({ 'kline_watchlist_v1': JSON.stringify(['A','B','C']) });
    env.sandbox.confirm = () => true;

    let putCount = 0, putBody = null;
    setMockFetch(async (url, options) => {
      if (options?.method === 'PUT') {
        putCount++;
        putBody = JSON.parse(options.body);
        return { ok: true, status: 200, json: async () => ({}) };
      }
      return {
        ok: true, status: 200,
        json: async () => ({
          sha: 'remote789',
          content: Buffer.from(JSON.stringify({ watchlist: ['A','B','C','D'] })).toString('base64'),
        }),
      };
    });

    await runInSandbox('clearAllWatchlist()');

    const tombstones = JSON.parse(getLocalStorage()['kline_watchlist_tombstones_v1'] || '[]');
    assertEqual(tombstones.length, 4, '应为4只股票创建tombstone');
    assert(tombstones.find(t => t.code === 'D'), 'D（远程独有）应有tombstone');

    const decoded = JSON.parse(Buffer.from(putBody.content, 'base64').toString('utf8'));
    assertEqual(decoded.watchlist, [], 'PUT内容watchlist应为空');
    assert(decoded.tombstones.find(t => t.code === 'D'), 'PUT内容tombstone应包含D');
    console.log(`  📊 远程有独有D，全局清空后D也被tombstone: YES`);
  }

  // 场景H: 并发添加期间的全局清空 → stale SHA重试，新代码也被tombstone（Final blocker）
  console.log('\n【场景H】并发添加期间全局清空 → stale SHA重试，新代码C也被tombstone（Final blocker）');
  {
    resetMocks();
    setLocalStorage({ 'kline_watchlist_v1': JSON.stringify(['A','B']) });
    env.sandbox.confirm = () => true;

    let getCount = 0, putCount = 0, putBody = null;
    setMockFetch(async (url, options) => {
      if (options?.method === 'PUT') {
        putCount++;
        putBody = JSON.parse(options.body);
        if (putCount === 1) {
          // 第一次PUT返回409（另一台设备刚添加了C）
          return { ok: false, status: 409, json: async () => ({ message: 'SHA mismatch' }) };
        }
        // 第二次PUT成功
        return { ok: true, status: 200, json: async () => ({}) };
      }
      // GET
      getCount++;
      if (getCount === 1) {
        // 第一次GET：远程[A,B]
        return {
          ok: true, status: 200,
          json: async () => ({
            sha: 'oldsha111',
            content: Buffer.from(JSON.stringify({ watchlist: ['A','B'] })).toString('base64'),
          }),
        };
      }
      // 第二次GET（重试时）：远程已变成[A,B,C]（另一台设备添加了C）
      return {
        ok: true, status: 200,
        json: async () => ({
          sha: 'newsha222',
          content: Buffer.from(JSON.stringify({ watchlist: ['A','B','C'] })).toString('base64'),
        }),
      };
    });

    await runInSandbox('clearAllWatchlist()');

    // 验证：GET=2, PUT=2
    assertEqual(getCount, 2, '应执行2次GET（初始 + 冲突重试时重新拉取）');
    assertEqual(putCount, 2, '应执行2次PUT（第一次409 + 第二次成功）');

    // 验证：最终PUT内容的tombstone包含A/B/C（C是重试时新发现的）
    const decoded = JSON.parse(Buffer.from(putBody.content, 'base64').toString('utf8'));
    assertEqual(decoded.watchlist, [], '最终PUT内容watchlist应为空');
    assert(decoded.tombstones.find(t => t.code === 'A'), 'tombstone应包含A');
    assert(decoded.tombstones.find(t => t.code === 'B'), 'tombstone应包含B');
    assert(decoded.tombstones.find(t => t.code === 'C'), 'tombstone应包含C（并发添加的新代码，重试时发现）');

    // 验证：本地已清空
    const watchlist = JSON.parse(getLocalStorage()['kline_watchlist_v1']);
    assertEqual(watchlist, [], '本地watchlist应为空');

    console.log(`  📊 GET:${getCount} PUT:${putCount}, 并发添加的C也被tombstone: ${decoded.tombstones.find(t => t.code === 'C') ? 'YES' : 'NO'}`);
  }

  // 场景I: 远程不可读（无效JSON）→ 中止，本地不变，PUT=0（Final blocker）
  console.log('\n【场景I】远程不可读（无效JSON）→ 中止，本地不变，PUT=0（Final blocker）');
  {
    resetMocks();
    setLocalStorage({ 'kline_watchlist_v1': JSON.stringify(['X','Y','Z']) });
    env.sandbox.confirm = () => true;

    let getCount = 0, putCount = 0;
    setMockFetch(async (url, options) => {
      if (options?.method === 'PUT') {
        putCount++;
        return { ok: true, status: 200, json: async () => ({}) };
      }
      getCount++;
      // 返回200但内容是无效JSON
      return {
        ok: true, status: 200,
        json: async () => ({
          sha: 'badsha999',
          content: Buffer.from('this is not valid json {{{').toString('base64'),
        }),
      };
    });

    await runInSandbox('clearAllWatchlist()');

    // 验证：GET=1, PUT=0（中止，不上传）
    assertEqual(getCount, 1, '应执行1次GET');
    assertEqual(putCount, 0, 'PUT次数应为0（远程解析失败后中止，不上传）');

    // 验证：本地状态不变（股票保留，tombstone不创建）
    const watchlist = JSON.parse(getLocalStorage()['kline_watchlist_v1']);
    assertEqual(watchlist, ['X','Y','Z'], '本地watchlist应保持不变（中止操作）');
    const tombstones = JSON.parse(getLocalStorage()['kline_watchlist_tombstones_v1'] || '[]');
    assertEqual(tombstones.length, 0, '不应创建tombstone（中止操作）');

    console.log(`  📊 GET:${getCount} PUT:${putCount}, 远程不可读时中止且本地不变: ${watchlist.length === 3 ? 'YES' : 'NO'}`);
  }

  // 场景J: Remote已有旧tombstone X → Clear All后X必须仍保留
  console.log('\n【场景J】Remote已有旧tombstone X → Clear All后X仍保留（字段保留）');
  {
    resetMocks();
    setLocalStorage({ 'kline_watchlist_v1': JSON.stringify(['A','B']) });
    env.sandbox.confirm = () => true;

    let putBody = null;
    setMockFetch(async (url, options) => {
      if (options?.method === 'PUT') {
        putBody = JSON.parse(options.body);
        return { ok: true, status: 200, json: async () => ({}) };
      }
      // 远程配置包含旧tombstone X
      return {
        ok: true, status: 200,
        json: async () => ({
          sha: 'sha111',
          content: Buffer.from(JSON.stringify({
            watchlist: ['A','B'],
            tombstones: [{ code: 'X', deletedAt: '2026-09-01T00:00:00.000Z' }],
          })).toString('base64'),
        }),
      };
    });

    await runInSandbox('clearAllWatchlist()');

    const decoded = JSON.parse(Buffer.from(putBody.content, 'base64').toString('utf8'));
    // 验证：旧tombstone X仍保留
    assert(decoded.tombstones.find(t => t.code === 'X'), '远程旧tombstone X必须仍保留');
    assertEqual(decoded.tombstones.find(t => t.code === 'X').deletedAt, '2026-09-01T00:00:00.000Z', '旧tombstone X的deletedAt应保持不变');
    // 验证：A/B也被tombstone
    assert(decoded.tombstones.find(t => t.code === 'A'), 'A应有tombstone');
    assert(decoded.tombstones.find(t => t.code === 'B'), 'B应有tombstone');
    assertEqual(decoded.watchlist, [], 'watchlist应为空');

    console.log(`  📊 远程旧tombstone X保留: ${decoded.tombstones.find(t => t.code === 'X') ? 'YES' : 'NO'}`);
  }

  // 场景K: Remote sector R + 本地sector L → Clear All后R/L都保留
  console.log('\n【场景K】Remote sector R + 本地sector L → Clear All后R/L都保留（customSectors merge）');
  {
    resetMocks();
    setLocalStorage({ 'kline_watchlist_v1': JSON.stringify(['A']) });
    // 本地customSectors包含L
    setLocalStorage({
      'kline_watchlist_v1': JSON.stringify(['A']),
      'kline_custom_sectors_v1': JSON.stringify([{ code: 'L', name: '本地板块' }]),
    });
    env.sandbox.confirm = () => true;

    let putBody = null;
    setMockFetch(async (url, options) => {
      if (options?.method === 'PUT') {
        putBody = JSON.parse(options.body);
        return { ok: true, status: 200, json: async () => ({}) };
      }
      // 远程配置包含sector R
      return {
        ok: true, status: 200,
        json: async () => ({
          sha: 'sha222',
          content: Buffer.from(JSON.stringify({
            watchlist: ['A'],
            customSectors: [{ code: 'R', name: '远程板块' }],
          })).toString('base64'),
        }),
      };
    });

    await runInSandbox('clearAllWatchlist()');

    const decoded = JSON.parse(Buffer.from(putBody.content, 'base64').toString('utf8'));
    // 验证：R和L都保留
    assert(decoded.customSectors.find(s => s.code === 'R'), '远程sector R必须保留');
    assert(decoded.customSectors.find(s => s.code === 'L'), '本地sector L必须保留');
    assertEqual(decoded.customSectors.length, 2, 'customSectors应包含R和L共2个');
    assertEqual(decoded.watchlist, [], 'watchlist应为空');

    console.log(`  📊 Remote R保留: ${decoded.customSectors.find(s => s.code === 'R') ? 'YES' : 'NO'}, Local L保留: ${decoded.customSectors.find(s => s.code === 'L') ? 'YES' : 'NO'}`);
  }

  // 场景L: scanMinScore / scanSignalType → Clear All后值不变
  console.log('\n【场景L】scanMinScore / scanSignalType → Clear All后值不变（settings原样保留）');
  {
    resetMocks();
    setLocalStorage({ 'kline_watchlist_v1': JSON.stringify(['A']) });
    env.sandbox.confirm = () => true;

    let putBody = null;
    setMockFetch(async (url, options) => {
      if (options?.method === 'PUT') {
        putBody = JSON.parse(options.body);
        return { ok: true, status: 200, json: async () => ({}) };
      }
      // 远程配置包含settings
      return {
        ok: true, status: 200,
        json: async () => ({
          sha: 'sha333',
          content: Buffer.from(JSON.stringify({
            watchlist: ['A'],
            settings: { scanMinScore: '75', scanSignalType: 'bear' },
          })).toString('base64'),
        }),
      };
    });

    await runInSandbox('clearAllWatchlist()');

    const decoded = JSON.parse(Buffer.from(putBody.content, 'base64').toString('utf8'));
    // 验证：settings值不变
    assert(decoded.settings, 'settings字段必须存在');
    assertEqual(decoded.settings.scanMinScore, '75', 'scanMinScore应保持75不变');
    assertEqual(decoded.settings.scanSignalType, 'bear', 'scanSignalType应保持bear不变');
    assertEqual(decoded.watchlist, [], 'watchlist应为空');

    console.log(`  📊 scanMinScore=${decoded.settings?.scanMinScore}, scanSignalType=${decoded.settings?.scanSignalType}（应不变）`);
  }

  // 场景M: 未知未来字段schemaExtra → Clear All后仍存在
  console.log('\n【场景M】未知未来字段schemaExtra → Clear All后仍存在（前向兼容）');
  {
    resetMocks();
    setLocalStorage({ 'kline_watchlist_v1': JSON.stringify(['A']) });
    env.sandbox.confirm = () => true;

    let putBody = null;
    setMockFetch(async (url, options) => {
      if (options?.method === 'PUT') {
        putBody = JSON.parse(options.body);
        return { ok: true, status: 200, json: async () => ({}) };
      }
      // 远程配置包含未知未来字段
      return {
        ok: true, status: 200,
        json: async () => ({
          sha: 'sha444',
          content: Buffer.from(JSON.stringify({
            watchlist: ['A'],
            schemaExtra: { version: 'v99', futureFeature: true, nested: { key: 'value' } },
            anotherUnknownField: 'some data',
          })).toString('base64'),
        }),
      };
    });

    await runInSandbox('clearAllWatchlist()');

    const decoded = JSON.parse(Buffer.from(putBody.content, 'base64').toString('utf8'));
    // 验证：未知未来字段仍存在
    assert(decoded.schemaExtra, '未知未来字段schemaExtra必须仍存在');
    assertEqual(decoded.schemaExtra.version, 'v99', 'schemaExtra.version应保持不变');
    assertEqual(decoded.schemaExtra.futureFeature, true, 'schemaExtra.futureFeature应保持不变');
    assertEqual(decoded.schemaExtra.nested.key, 'value', 'schemaExtra.nested.key应保持不变');
    assertEqual(decoded.anotherUnknownField, 'some data', 'anotherUnknownField应保持不变');
    assertEqual(decoded.watchlist, [], 'watchlist应为空');

    console.log(`  📊 schemaExtra保留: ${decoded.schemaExtra ? 'YES' : 'NO'}, anotherUnknownField保留: ${decoded.anotherUnknownField ? 'YES' : 'NO'}`);
  }

  // ========== 总结 ==========
  console.log('\n' + '='.repeat(60));
  console.log(`测试结果: ${passed} 通过, ${failed} 失败`);
  console.log('='.repeat(60));

  if (failed > 0) {
    console.error('\n失败的测试:');
    failures.forEach((f, i) => console.error(`  ${i+1}. ${f}`));
    process.exit(1);
  }

  console.log('\n🎉 全部测试通过！（FIR-20 删除语义修复验证）');
  process.exit(0);
}

runTests().catch(e => {
  console.error('测试执行出错:', e.message);
  console.error(e.stack);
  process.exit(1);
});
