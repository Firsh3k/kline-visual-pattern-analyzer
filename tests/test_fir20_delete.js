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

  // 场景D: 全局清空 → 所有股票创建tombstone
  console.log('\n【场景D】全局清空 → 所有股票创建tombstone（QA场景D）');
  {
    resetMocks();
    setLocalStorage({ 'kline_watchlist_v1': JSON.stringify(['A1','A2','A3']) });
    env.sandbox.confirm = () => true;

    // mock fetch：远程无配置（404）
    setMockFetch(async (url, options) => {
      return { ok: false, status: 404, json: async () => ({ message: 'Not Found' }) };
    });

    await runInSandbox('clearAllWatchlist()');

    const tombstones = JSON.parse(getLocalStorage()['kline_watchlist_tombstones_v1'] || '[]');
    assertEqual(tombstones.length, 3, '应为3只股票创建tombstone');
    assert(tombstones.find(t => t.code === 'A1'), 'A1应有tombstone');
    assert(tombstones.find(t => t.code === 'A2'), 'A2应有tombstone');
    assert(tombstones.find(t => t.code === 'A3'), 'A3应有tombstone');

    const watchlist = JSON.parse(getLocalStorage()['kline_watchlist_v1']);
    assertEqual(watchlist, [], '本地watchlist应为空');
  }

  // 场景E: 取消清空 → 不做任何操作（股票保留）
  console.log('\n【场景E】取消清空 → 中止操作，股票保留（Blocker 3修复）');
  {
    resetMocks();
    setLocalStorage({ 'kline_watchlist_v1': JSON.stringify(['B1','B2','B3']) });
    env.sandbox.confirm = () => false; // 用户取消

    await runInSandbox('clearAllWatchlist()');

    const tombstones = JSON.parse(getLocalStorage()['kline_watchlist_tombstones_v1'] || '[]');
    assertEqual(tombstones.length, 0, '取消清空不应创建tombstone');

    const watchlist = JSON.parse(getLocalStorage()['kline_watchlist_v1']);
    assertEqual(watchlist, ['B1','B2','B3'], '取消清空后股票应保留（不做任何操作）');
  }

  // 场景F: 取消删除（单只）→ 不创建tombstone，股票保留
  console.log('\n【场景F】取消删除（单只）→ 不创建tombstone，股票保留');
  {
    resetMocks();
    setLocalStorage({ 'kline_watchlist_v1': JSON.stringify(['C1','C2','C3']) });
    env.sandbox.confirm = () => false; // 用户取消

    runInSandbox('removeStock("C2")');

    const tombstones = JSON.parse(getLocalStorage()['kline_watchlist_tombstones_v1'] || '[]');
    assertEqual(tombstones.length, 0, '取消删除不应创建tombstone');

    const watchlist = JSON.parse(getLocalStorage()['kline_watchlist_v1']);
    assertEqual(watchlist, ['C1','C2','C3'], '取消删除后股票应保留');
  }

  // 场景G: 全局清空（远程有独有股票）→ 为remote ∪ local创建tombstone（Blocker 2修复）
  console.log('\n【场景G】全局清空（远程有独有股票）→ 为remote∪local创建tombstone（Blocker 2）');
  {
    resetMocks();
    // 本地有[A,B,C]，远程有[A,B,C,D]（D是远程独有）
    setLocalStorage({ 'kline_watchlist_v1': JSON.stringify(['A','B','C']) });
    env.sandbox.confirm = () => true;

    // mock fetch：GET返回远程配置（包含D），PUT成功
    let putCount = 0, putBody = null;
    setMockFetch(async (url, options) => {
      if (options?.method === 'PUT') {
        putCount++;
        putBody = JSON.parse(options.body);
        return { ok: true, status: 200, json: async () => ({}) };
      }
      // GET返回远程配置（包含D，远程独有）
      return {
        ok: true, status: 200,
        json: async () => ({
          sha: 'remote789',
          content: Buffer.from(JSON.stringify({ watchlist: ['A','B','C','D'] })).toString('base64'),
        }),
      };
    });

    await runInSandbox('clearAllWatchlist()');

    // 验证tombstone包含A/B/C/D（远程独有D也被tombstone）
    const tombstones = JSON.parse(getLocalStorage()['kline_watchlist_tombstones_v1'] || '[]');
    assertEqual(tombstones.length, 4, '应为4只股票创建tombstone（远程3+本地3，去重后4）');
    assert(tombstones.find(t => t.code === 'A'), 'A应有tombstone');
    assert(tombstones.find(t => t.code === 'B'), 'B应有tombstone');
    assert(tombstones.find(t => t.code === 'C'), 'C应有tombstone');
    assert(tombstones.find(t => t.code === 'D'), 'D（远程独有）应有tombstone（Blocker 2核心修复）');

    // 验证本地已清空
    const watchlist = JSON.parse(getLocalStorage()['kline_watchlist_v1']);
    assertEqual(watchlist, [], '本地watchlist应为空');

    // 验证同步后远程也不包含D
    if (putBody) {
      const decoded = JSON.parse(Buffer.from(putBody.content, 'base64').toString('utf8'));
      assertEqual(decoded.watchlist, [], '同步后远程watchlist应为空');
      assert(decoded.tombstones && decoded.tombstones.find(t => t.code === 'D'), 'tombstone应同步到远程，包含D');
    }

    console.log(`  📊 远程有独有D，全局清空后D也被tombstone: ${tombstones.find(t => t.code === 'D') ? 'YES' : 'NO'}`);
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
