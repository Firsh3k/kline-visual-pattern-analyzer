#!/usr/bin/env node
/**
 * FIR-42：在妙搭真实运行环境里检查自选股数据库是否可用（部署前由开发执行）。
 *
 *   在平台工程目录（v6/app-builder，能找到 node_modules/postgres 的地方）执行：
 *   node scripts/watchlist-probe.js
 *
 * 用和应用相同的连接串（WATCHLIST_DATABASE_URL 或 SUDA_DATABASE_URL）与相同的 postgres 驱动，检查：
 *   1. 能连上（SELECT 1）
 *   2. 两张表已由平台 migration 建好，应用账号有 SELECT / INSERT / UPDATE
 *   3. 版本行存在；应用实际用的一致读取 SQL 能执行
 *   4. 写事务的第一句（UPDATE … RETURNING）能在连接池下执行，回滚后版本不变
 *   5. 两个连接同时写时，第二个会等第一个提交 / 回滚（行锁串行）——这是多端同步版本号正确的前提
 * 所有写入都在事务里并且最后回滚，不改动任何数据。输出 JSON；不打印连接串。
 * 全部通过 exit 0，否则 exit 1。
 */
'use strict';

const url = process.env.WATCHLIST_DATABASE_URL || process.env.SUDA_DATABASE_URL;
const table = process.env.WATCHLIST_TABLE || 'kline_watchlist';   // 仅测试时覆盖
const meta = `${table}_meta`;
const results = [];
const scrub = (m) => (url ? String(m).split(url).join('***') : String(m));

function record(name, ok, detail) {
  results.push({ check: name, ok, ...(detail === undefined ? {} : { detail }) });
  return ok;
}

async function main() {
  if (!url) {
    record('连接串', false, '未设置 SUDA_DATABASE_URL / WATCHLIST_DATABASE_URL');
    return;
  }
  if (!/^[a-z_][a-z0-9_]*$/.test(table)) {
    record('表名', false, `非法表名：${table}`);
    return;
  }
  let postgres;
  try {
    postgres = require('postgres');
    record('驱动', true, require.resolve('postgres'));
  } catch (e) {
    record('驱动', false, scrub(e.message));
    return;
  }
  // 与应用 PG_CLIENT_OPTIONS 保持一致
  const opts = { max: 1, idle_timeout: 30, connect_timeout: 10, onnotice: () => {} };
  const a = postgres(url, opts);
  const b = postgres(url, opts);
  try {
    await a`SELECT 1`;
    record('连接', true);

    const [reg] = await a`SELECT to_regclass(${table}) IS NOT NULL AS t, to_regclass(${meta}) IS NOT NULL AS m`;
    if (!record('表已建好', reg.t && reg.m, { [table]: reg.t, [meta]: reg.m })) return;

    const [p] = await a`
      SELECT has_table_privilege(${table}, 'SELECT') AS ts, has_table_privilege(${table}, 'INSERT') AS ti,
             has_table_privilege(${table}, 'UPDATE') AS tu,
             has_table_privilege(${meta}, 'SELECT') AS ms, has_table_privilege(${meta}, 'INSERT') AS mi,
             has_table_privilege(${meta}, 'UPDATE') AS mu`;
    if (!record('应用账号权限', Object.values(p).every(Boolean), p)) return;

    const rows = await a`SELECT rev FROM ${a(meta)} WHERE id = 1`;
    if (!record('版本行', rows.length === 1, rows.length ? { rev: Number(rows[0].rev) } : '缺失（应用 init 会补一行）')) return;
    const rev0 = Number(rows[0].rev);

    const snap = await a`
      SELECT m.rev AS meta_rev, w.code, w.state FROM ${a(meta)} m LEFT JOIN ${a(table)} w ON true WHERE m.id = 1`;
    record('一致读取', snap.length >= 1, { rows: snap.filter((r) => r.code !== null).length });

    class Rollback extends Error {}
    // 4. 写事务第一句 + 回滚
    let bumped = null;
    try {
      await a.begin(async (tx) => {
        const [r] = await tx`UPDATE ${tx(meta)} SET rev = rev + 1 WHERE id = 1 RETURNING rev`;
        bumped = Number(r.rev);
        throw new Rollback();
      });
    } catch (e) {
      if (!(e instanceof Rollback)) throw e;
    }
    const [after] = await a`SELECT rev FROM ${a(meta)} WHERE id = 1`;
    if (!record('写事务（UPDATE … RETURNING）+ 回滚', bumped === rev0 + 1 && Number(after.rev) === rev0,
      { before: rev0, in_tx: bumped, after_rollback: Number(after.rev) })) return;

    // 4b. 应用实际用到的写语句（加入 / 删除记录 / 清空 / 迁入），全部在事务里执行后回滚
    const probeCode = '000000';
    const seen = {};
    try {
      await a.begin(async (tx) => {
        const now = new Date();
        const [r] = await tx`UPDATE ${tx(meta)} SET rev = rev + 1 WHERE id = 1 RETURNING rev`;
        const rev = Number(r.rev);
        await tx`SELECT state FROM ${tx(table)} WHERE code = ${probeCode}`;
        const [{ n }] = await tx`SELECT count(*)::int AS n FROM ${tx(table)} WHERE state = 'active'`;
        seen.active_count = n;
        await tx`
          INSERT INTO ${tx(table)} (code, state, added_at, deleted_at, updated_at, rev)
          VALUES (${probeCode}, 'active', ${now}, NULL, ${now}, ${rev})
          ON CONFLICT (code) DO UPDATE
            SET state = 'active', added_at = EXCLUDED.added_at, deleted_at = NULL,
                updated_at = EXCLUDED.updated_at, rev = EXCLUDED.rev`;
        const del = await tx`
          INSERT INTO ${tx(table)} (code, state, added_at, deleted_at, updated_at, rev)
          VALUES (${probeCode}, 'deleted', ${now}, ${now}, ${now}, ${rev})
          ON CONFLICT (code) DO UPDATE
            SET state = 'deleted', deleted_at = EXCLUDED.deleted_at, updated_at = EXCLUDED.updated_at, rev = EXCLUDED.rev
            WHERE ${tx(table)}.updated_at <= EXCLUDED.updated_at
          RETURNING code`;
        seen.upsert_returning = del.length;
        const cl = await tx`
          UPDATE ${tx(table)} SET state = 'deleted', deleted_at = ${now}, updated_at = ${now}, rev = ${rev}
          WHERE code = ${probeCode} RETURNING code`;
        seen.update_returning = cl.length;
        throw new Rollback();
      });
    } catch (e) {
      if (!(e instanceof Rollback)) throw e;
    }
    const [left] = await a`SELECT count(*)::int AS n FROM ${a(table)} WHERE code = ${probeCode}`;
    const [rev1] = await a`SELECT rev FROM ${a(meta)} WHERE id = 1`;
    if (!record('应用写语句（回滚）', seen.upsert_returning === 1 && seen.update_returning === 1 && left.n === 0 && Number(rev1.rev) === rev0,
      { ...seen, probe_rows_left: left.n, rev_after: Number(rev1.rev) })) return;

    // 5. 两个连接：A 持锁时 B 必须等待
    let releaseA;
    const aHolding = new Promise((r) => { releaseA = r; });
    let aLocked;
    const aLockedP = new Promise((r) => { aLocked = r; });
    const order = [];
    const ta = a.begin(async (tx) => {
      await tx`UPDATE ${tx(meta)} SET rev = rev + 1 WHERE id = 1 RETURNING rev`;
      order.push('A 拿到锁');
      aLocked();
      await aHolding;
      order.push('A 回滚');
      throw new Rollback();
    }).catch((e) => { if (!(e instanceof Rollback)) throw e; });
    await aLockedP;
    let bDone = false;
    const tb = b.begin(async (tx) => {
      await tx`UPDATE ${tx(meta)} SET rev = rev + 1 WHERE id = 1 RETURNING rev`;
      bDone = true;
      order.push('B 拿到锁');
      throw new Rollback();
    }).catch((e) => { if (!(e instanceof Rollback)) throw e; });
    await new Promise((r) => setTimeout(r, 1500));
    const bWaited = !bDone;
    releaseA();
    await Promise.race([Promise.all([ta, tb]), new Promise((_, rej) => setTimeout(() => rej(new Error('等待 B 超时（15 秒）')), 15000))]);
    const [fin] = await a`SELECT rev FROM ${a(meta)} WHERE id = 1`;
    record('两个连接写入串行', bWaited && order.join(' → ') === 'A 拿到锁 → A 回滚 → B 拿到锁' && Number(fin.rev) === rev0,
      { b_waited_while_a_held_lock: bWaited, order: order.join(' → '), rev_after: Number(fin.rev) });
  } catch (e) {
    record('执行出错', false, { code: e.code, message: scrub(e.message) });
  } finally {
    await Promise.allSettled([a.end({ timeout: 5 }), b.end({ timeout: 5 })]);
  }
}

main()
  .catch((e) => record('执行出错', false, scrub(e && e.message)))
  .finally(() => {
    const ok = results.length > 0 && results.every((r) => r.ok);
    console.log(JSON.stringify({ ok, table, results }, null, 2));
    process.exit(ok ? 0 : 1);
  });
