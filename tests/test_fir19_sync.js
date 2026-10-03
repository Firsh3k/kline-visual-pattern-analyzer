/**
 * FIR-19 多设备自选股同步回归测试
 * 使用从 kline_analyzer.html 提取的真实生产函数（非复制实现）
 *
 * 运行方式: node tests/test_fir19_sync.js
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
console.log('FIR-19 多设备自选股同步回归测试（生产代码路径）');
console.log('='.repeat(60));

const env = createTestSandbox();
const { callHistory, setMockFetch, resetMocks, getLocalStorage, setLocalStorage, runInSandbox } = env;
const prod = loadProductionCode(env.sandbox);

assert(typeof prod.mergeWatchlistsWithTombstones === 'function', '生产函数 mergeWatchlistsWithTombstones 已加载');
assert(typeof prod.mergeCustomSectors === 'function', '生产函数 mergeCustomSectors 已加载');
assert(typeof prod.fetchRemoteGithubConfig === 'function', '生产函数 fetchRemoteGithubConfig 已加载');
assert(typeof prod.syncToGithub === 'function', '生产函数 syncToGithub 已加载');
assert(typeof prod.deleteFromAllDevices === 'function', '生产函数 deleteFromAllDevices 已加载');

// Mock showConfirm（自定义确认对话框，直接返回true）
env.sandbox.showConfirm = async () => true;

const { mergeWatchlistsWithTombstones, mergeCustomSectors } = prod;

// ========== 第一部分：纯函数测试 ==========
console.log('\n--- 第一部分：纯函数逻辑测试 ---');

console.log('\n【场景A】remote superset (remote 10 + local 5 → 10)');
{
  const r = mergeWatchlistsWithTombstones(['A1','A2','A3','A4','A5','A6','A7','A8','A9','A10'], ['A1','A2','A3','A4','A5'], [], []);
  assertEqual(r.mergedWatchlist.length, 10, '合并后应为10只');
  assertEqual(r.onlyRemote.length, 5, 'onlyRemote应为5只');
  assertEqual(r.onlyLocal.length, 0, 'onlyLocal应为0只');
}

console.log('\n【场景B】local additions');
{
  const r = mergeWatchlistsWithTombstones(['A1','A2','A3'], ['A1','B1','B2'], [], []);
  assertEqual(r.mergedWatchlist.length, 5, '合并后应为5只');
  assert(r.mergedWatchlist.includes('B1') && r.mergedWatchlist.includes('B2'), '本地新增应保留');
}

console.log('\n【场景C】两设备独立添加');
{
  const r = mergeWatchlistsWithTombstones(['BASE1','X'], ['BASE1','Y'], [], []);
  assertEqual(r.mergedWatchlist.length, 3, '合并后应为3只');
  assert(r.mergedWatchlist.includes('X') && r.mergedWatchlist.includes('Y'), 'X和Y都应保留');
}

console.log('\n【场景D】重复去重');
{
  const r = mergeWatchlistsWithTombstones(['A1','A1','A2'], ['A2','A3'], [], []);
  assertEqual(r.mergedWatchlist, ['A1','A2','A3'], '去重后顺序正确');
}

console.log('\n【场景E】tombstone阻止复活');
{
  const r = mergeWatchlistsWithTombstones(['A1','X'], ['A1'], [{code:'X',deletedAt:'2026-10-01'}], []);
  assertEqual(r.mergedWatchlist, ['A1'], 'X应被tombstone阻止');
  assert(r.resurrected.includes('X'), '应记录X被阻止复活');
}

console.log('\n【场景F】tombstone合并取并集');
{
  const r = mergeWatchlistsWithTombstones([], [], [{code:'X',deletedAt:'2026-10-01'}], [{code:'Y',deletedAt:'2026-10-02'}]);
  assertEqual(r.mergedTombstones.length, 2, '应有2条tombstone');
}

console.log('\n【场景G】空输入处理');
{
  const r = mergeWatchlistsWithTombstones(null, null, null, null);
  assertEqual(r.mergedWatchlist, [], '全null应返回空数组');
}

console.log('\n【场景H】customSectors合并');
{
  const r = mergeCustomSectors([{code:'S1'},{code:'S2'}], [{code:'S2'},{code:'S3'}]);
  assertEqual(r.length, 3, '合并后应有3个板块');
}

console.log('\n【场景I】tombstone最新时间优先');
{
  const r = mergeWatchlistsWithTombstones([], [], [{code:'X',deletedAt:'2026-10-01'}], [{code:'X',deletedAt:'2026-10-03'}]);
  assertEqual(r.mergedTombstones.length, 1, '同一code只保留1条');
  assertEqual(r.mergedTombstones[0].deletedAt, '2026-10-03', '保留最新deletedAt');
}

// ========== 第二部分：网络流测试 ==========
async function runNetworkTests() {
  console.log('\n--- 第二部分：网络流测试（syncToGithub真实生产代码路径） ---');

  // 场景J: stale SHA冲突重试
  console.log('\n【场景J】stale SHA冲突 (409 → re-fetch → re-merge → 重试成功)');
  {
    resetMocks();
    setLocalStorage({ 'kline_watchlist_v1': JSON.stringify(['LOCAL1','LOCAL2']) });
    let putCount = 0, getCount = 0;
    setMockFetch(async (url, options) => {
      if (options?.method === 'PUT') {
        putCount++;
        if (putCount === 1) return { ok:false, status:409, json:async()=>({message:'SHA mismatch'}) };
        return { ok:true, status:200, json:async()=>({commit:{sha:'ok'}}) };
      }
      getCount++;
      const list = getCount === 1 ? ['R1','R2','R3','R4','R5'] : ['R1','R2','R3','R4','R5','R6'];
      return { ok:true, status:200, json:async()=>({ sha:'sha'+getCount, content:Buffer.from(JSON.stringify({watchlist:list})).toString('base64') }) };
    });
    await runInSandbox('syncToGithub()');
    assertEqual(getCount, 2, '应执行2次GET');
    assertEqual(putCount, 2, '应执行2次PUT');
    const saved = JSON.parse(getLocalStorage()['kline_watchlist_v1']);
    assertEqual(saved.length, 8, '最终本地应保存8只');
    assert(saved.includes('R6'), '重试时拉取的R6应包含');
    console.log(`  📊 GET:${getCount} PUT:${putCount}`);
  }

  // 场景K: 远程解析失败中止
  console.log('\n【场景K】远程解析失败 (HTTP200+无效JSON → 中止，PUT=0)');
  {
    resetMocks();
    setLocalStorage({ 'kline_watchlist_v1': JSON.stringify(['LOCAL1']) });
    let putCount = 0, getCount = 0;
    setMockFetch(async (url, options) => {
      if (options?.method === 'PUT') { putCount++; return { ok:true, status:200, json:async()=>({}) }; }
      getCount++;
      return { ok:true, status:200, json:async()=>({ sha:'bad', content:Buffer.from('invalid json {{{').toString('base64') }) };
    });
    await runInSandbox('syncToGithub()');
    assertEqual(getCount, 1, '应执行1次GET');
    assertEqual(putCount, 0, 'PUT次数应为0（解析失败中止）');
    const saved = JSON.parse(getLocalStorage()['kline_watchlist_v1']);
    assertEqual(saved, ['LOCAL1'], '本地watchlist不应被修改');
    console.log(`  📊 GET:${getCount} PUT:${putCount}`);
  }

  // 场景L: 首次同步404创建
  console.log('\n【场景L】首次同步 (远程404 → 使用本地配置创建)');
  {
    resetMocks();
    setLocalStorage({ 'kline_watchlist_v1': JSON.stringify(['L1','L2','L3']) });
    let putCount = 0, putBody = null;
    setMockFetch(async (url, options) => {
      if (options?.method === 'PUT') { putCount++; putBody = JSON.parse(options.body); return { ok:true, status:201, json:async()=>({}) }; }
      return { ok:false, status:404, json:async()=>({message:'Not Found'}) };
    });
    await runInSandbox('syncToGithub()');
    assertEqual(putCount, 1, '应执行1次PUT');
    assert(!putBody.sha, '首次创建不应传sha');
    const decoded = JSON.parse(Buffer.from(putBody.content,'base64').toString('utf8'));
    assertEqual(decoded.watchlist, ['L1','L2','L3'], 'PUT内容应等于本地watchlist');
    console.log(`  📊 PUT:${putCount}`);
  }

  // 场景M: deleteFromAllDevices
  console.log('\n【场景M】从所有设备删除 (deleteFromAllDevices)');
  {
    resetMocks();
    setLocalStorage({ 'kline_watchlist_v1': JSON.stringify(['A1','A2','A3']) });
    await runInSandbox('deleteFromAllDevices("A2")');
    const tombstones = JSON.parse(getLocalStorage()['kline_watchlist_tombstones_v1'] || '[]');
    assertEqual(tombstones.length, 1, '应添加1条tombstone');
    assertEqual(tombstones[0].code, 'A2', 'tombstone code应为A2');
    const watchlist = JSON.parse(getLocalStorage()['kline_watchlist_v1']);
    assertEqual(watchlist, ['A1','A3'], 'A2应从本地移除');
  }
}

// ========== 执行并总结 ==========
runNetworkTests().then(() => {
  console.log('\n' + '='.repeat(60));
  console.log(`测试结果: ${passed} 通过, ${failed} 失败`);
  console.log('='.repeat(60));
  if (failed > 0) {
    console.error('\n失败的测试:');
    failures.forEach((f,i) => console.error(`  ${i+1}. ${f}`));
    process.exit(1);
  }
  console.log('\n🎉 全部测试通过！（使用真实生产代码路径）');
  process.exit(0);
}).catch(e => {
  console.error('测试执行出错:', e.message);
  console.error(e.stack);
  process.exit(1);
});
