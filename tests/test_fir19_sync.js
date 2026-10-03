/**
 * FIR-19 多设备自选股同步回归测试
 * 覆盖QA要求的7个确定性场景
 *
 * 运行方式: node tests/test_fir19_sync.js
 */

// ========== 从kline_analyzer.html提取的纯函数（与生产代码保持一致） ==========

function mergeWatchlistsWithTombstones(remoteWatchlist, localWatchlist, remoteTombstones, localTombstones) {
  remoteWatchlist = remoteWatchlist || [];
  localWatchlist = localWatchlist || [];
  remoteTombstones = remoteTombstones || [];
  localTombstones = localTombstones || [];

  const tombstoneMap = new Map();
  for (const t of [...remoteTombstones, ...localTombstones]) {
    if (t && t.code) {
      const existing = tombstoneMap.get(t.code);
      if (!existing || (t.deletedAt && t.deletedAt > existing.deletedAt)) {
        tombstoneMap.set(t.code, t);
      }
    }
  }
  const mergedTombstones = Array.from(tombstoneMap.values());
  const deletedCodes = new Set(mergedTombstones.map(t => t.code));

  const seen = new Set();
  const mergedWatchlist = [];
  const resurrected = [];

  for (const code of [...remoteWatchlist, ...localWatchlist]) {
    if (!code || seen.has(code)) continue;
    seen.add(code);
    if (deletedCodes.has(code)) {
      resurrected.push(code);
      continue;
    }
    mergedWatchlist.push(code);
  }

  const onlyRemote = remoteWatchlist.filter(c => c && !localWatchlist.includes(c) && !deletedCodes.has(c));
  const onlyLocal = localWatchlist.filter(c => c && !remoteWatchlist.includes(c) && !deletedCodes.has(c));

  return {
    mergedWatchlist,
    mergedTombstones,
    onlyRemote,
    onlyLocal,
    resurrected,
    deletedCount: deletedCodes.size
  };
}

function mergeCustomSectors(remoteSectors, localSectors) {
  remoteSectors = remoteSectors || [];
  localSectors = localSectors || [];
  const merged = [];
  const seen = new Set();
  for (const s of [...remoteSectors, ...localSectors]) {
    const key = (s && (s.code || s.name)) || JSON.stringify(s);
    if (key && !seen.has(key)) {
      seen.add(key);
      merged.push(s);
    }
  }
  return merged;
}

// ========== 测试框架 ==========
let passed = 0;
let failed = 0;
const failures = [];

function assert(condition, message) {
  if (condition) {
    passed++;
    console.log(`  ✅ ${message}`);
  } else {
    failed++;
    failures.push(message);
    console.error(`  ❌ ${message}`);
  }
}

function assertEqual(actual, expected, message) {
  const actualStr = JSON.stringify(actual);
  const expectedStr = JSON.stringify(expected);
  if (actualStr === expectedStr) {
    passed++;
    console.log(`  ✅ ${message}`);
  } else {
    failed++;
    failures.push(`${message}\n     期望: ${expectedStr}\n     实际: ${actualStr}`);
    console.error(`  ❌ ${message}\n     期望: ${expectedStr}\n     实际: ${actualStr}`);
  }
}

// ========== 测试场景 ==========

console.log('\n' + '='.repeat(60));
console.log('FIR-19 多设备自选股同步回归测试');
console.log('='.repeat(60));

// 场景A: remote superset — remote 10 + local 5 → 10
console.log('\n【场景A】remote superset (remote 10 + local 5 → 10)');
{
  const remote = ['A1','A2','A3','A4','A5','A6','A7','A8','A9','A10'];
  const local = ['A1','A2','A3','A4','A5'];
  const result = mergeWatchlistsWithTombstones(remote, local, [], []);
  assertEqual(result.mergedWatchlist.length, 10, '合并后应为10只（不缩减）');
  assertEqual(result.mergedWatchlist, remote, '合并后应等于远程的10只');
  assertEqual(result.onlyRemote.length, 5, 'onlyRemote应为5只（A6-A10）');
  assertEqual(result.onlyLocal.length, 0, 'onlyLocal应为0只');
}

// 场景B: local additions — local adds new stocks → union
console.log('\n【场景B】local additions (本地新增股票 → 并集)');
{
  const remote = ['A1','A2','A3','A4','A5'];
  const local = ['A1','A2','A3','B1','B2'];
  const result = mergeWatchlistsWithTombstones(remote, local, [], []);
  assertEqual(result.mergedWatchlist.length, 7, '合并后应为7只');
  assert(result.mergedWatchlist.includes('B1') && result.mergedWatchlist.includes('B2'), '本地新增的B1/B2应保留');
  assertEqual(result.onlyLocal.length, 2, 'onlyLocal应为2只（B1/B2）');
}

// 场景C: two devices independently add X/Y → both survive
console.log('\n【场景C】两设备独立添加 (A加X, B加Y → 两者都保留)');
{
  // 模拟设备A的状态：远程有基础股票，本地加了X
  const remoteAfterA = ['BASE1','BASE2','X'];
  const localB = ['BASE1','BASE2','Y'];
  const result = mergeWatchlistsWithTombstones(remoteAfterA, localB, [], []);
  assertEqual(result.mergedWatchlist.length, 4, '合并后应为4只');
  assert(result.mergedWatchlist.includes('X'), '设备A添加的X应保留');
  assert(result.mergedWatchlist.includes('Y'), '设备B添加的Y应保留');
  assertEqual(result.onlyRemote.length, 1, 'onlyRemote应为1只（X）');
  assertEqual(result.onlyLocal.length, 1, 'onlyLocal应为1只（Y）');
}

// 场景D: duplicates → de-duplicated
console.log('\n【场景D】重复条目 (去重)');
{
  const remote = ['A1','A1','A2','A2','A3'];
  const local = ['A2','A3','A3','A4'];
  const result = mergeWatchlistsWithTombstones(remote, local, [], []);
  assertEqual(result.mergedWatchlist.length, 4, '合并后应为4只（A1/A2/A3/A4）');
  assertEqual(result.mergedWatchlist, ['A1','A2','A3','A4'], '去重后顺序正确（远程在前）');
}

// 场景E: explicit delete → does not resurrect (Tombstone)
console.log('\n【场景E】显式删除 (tombstone阻止复活)');
{
  // 设备A删除了X，添加到tombstone
  const remote = ['A1','A2','X']; // 远程还有X（旧数据）
  const local = ['A1','A2']; // 本地已删除X
  const remoteTombstones = [{ code: 'X', deletedAt: '2026-10-03T00:00:00Z' }];
  const localTombstones = [{ code: 'X', deletedAt: '2026-10-03T00:00:00Z' }];
  const result = mergeWatchlistsWithTombstones(remote, local, remoteTombstones, localTombstones);
  assertEqual(result.mergedWatchlist.length, 2, '合并后应为2只（X被tombstone阻止）');
  assert(!result.mergedWatchlist.includes('X'), '已删除的X不应出现在watchlist中');
  assert(result.resurrected.includes('X'), '应记录X被阻止复活');
  assertEqual(result.mergedTombstones.length, 1, 'tombstone应保留1条');
}

// 场景F: tombstone合并（远程和本地都有tombstone，取并集）
console.log('\n【场景F】tombstone合并 (远程+本地tombstone取并集)');
{
  const remote = ['A1','A2'];
  const local = ['A1','A3'];
  const remoteTombstones = [{ code: 'X', deletedAt: '2026-10-01T00:00:00Z' }];
  const localTombstones = [{ code: 'Y', deletedAt: '2026-10-02T00:00:00Z' }];
  const result = mergeWatchlistsWithTombstones(remote, local, remoteTombstones, localTombstones);
  assertEqual(result.mergedTombstones.length, 2, '合并后应有2条tombstone（X和Y）');
  assert(result.mergedTombstones.find(t => t.code === 'X'), '远程tombstone X应保留');
  assert(result.mergedTombstones.find(t => t.code === 'Y'), '本地tombstone Y应保留');
}

// 场景G: 空输入处理
console.log('\n【场景G】空输入处理 (null/undefined/空数组)');
{
  const result1 = mergeWatchlistsWithTombstones(null, null, null, null);
  assertEqual(result1.mergedWatchlist, [], '全null应返回空数组');
  assertEqual(result1.mergedTombstones, [], '全null应返回空tombstone');

  const result2 = mergeWatchlistsWithTombstones([], [], [], []);
  assertEqual(result2.mergedWatchlist, [], '全空数组应返回空数组');

  const result3 = mergeWatchlistsWithTombstones(['A1'], undefined, undefined, []);
  assertEqual(result3.mergedWatchlist, ['A1'], '仅远程有数据应正常工作');
}

// 场景H: customSectors合并
console.log('\n【场景H】customSectors合并');
{
  const remote = [{ code: 'SEC1', name: '板块1' }, { code: 'SEC2', name: '板块2' }];
  const local = [{ code: 'SEC2', name: '板块2' }, { code: 'SEC3', name: '板块3' }];
  const result = mergeCustomSectors(remote, local);
  assertEqual(result.length, 3, '合并后应有3个板块');
}

// 场景I: tombstone最新时间优先
console.log('\n【场景I】tombstone最新时间优先 (同一code多条tombstone保留最新)');
{
  const remoteTombstones = [{ code: 'X', deletedAt: '2026-10-01T00:00:00Z' }];
  const localTombstones = [{ code: 'X', deletedAt: '2026-10-03T00:00:00Z' }];
  const result = mergeWatchlistsWithTombstones([], [], remoteTombstones, localTombstones);
  assertEqual(result.mergedTombstones.length, 1, '同一code应只保留1条tombstone');
  assertEqual(result.mergedTombstones[0].deletedAt, '2026-10-03T00:00:00Z', '应保留最新的deletedAt');
}

// ========== 总结 ==========
console.log('\n' + '='.repeat(60));
console.log(`测试结果: ${passed} 通过, ${failed} 失败`);
console.log('='.repeat(60));

if (failed > 0) {
  console.error('\n失败的测试:');
  failures.forEach((f, i) => console.error(`  ${i+1}. ${f}`));
  process.exit(1);
} else {
  console.log('\n🎉 全部测试通过！');
  process.exit(0);
}
