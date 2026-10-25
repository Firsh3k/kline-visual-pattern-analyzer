/**
 * FIR-41 第 4 项（第二部分）：自定义板块存到服务端（全站共用一份），手机 / 电脑同步，删除不复活。
 *
 * 与自选股（watchlist-store.ts）同一套做法：
 * - 一个板块一行：active（在用）/ deleted（删除记录，防止旧浏览器迁入时复活）。
 * - 生产用平台 PostgreSQL：运行时只做 DML，不建表（表由平台执行 custom-sectors.migration.sql）；
 *   写操作第一句 UPDATE 版本行拿锁并分配 rev，与数据同一事务提交；无改动回滚、rev 不变；读取一条 SQL（同一快照）。
 * - 未配置 / 驱动缺失 / 表不存在 / 运行中出错 → ready=false、接口 503，不影响其他功能；下一次调用自动重试。
 * - 自定义板块只是“扫描哪些股票”，不带来源、永远标“自定义”；每只股票的结论仍来自 Python Core。
 *
 * 规则：
 * - put：新建或替换成分（已删除的会重新启用）；成分完全相同则不改动。
 * - remove：写删除记录（之后的迁入不会把它带回来）。
 * - importLocal（浏览器一次性迁入）：先应用删除记录（只覆盖比它更早的服务器记录），
 *   再加入服务器上完全没有记录的板块（绝不覆盖已有的在用 / 删除记录）。
 */
import { WatchlistInputError, WatchlistUnavailableError } from './watchlist-store';

export const CS_TABLE = 'kline_custom_sectors';
export const MAX_CUSTOM_SECTORS = 50;
export const MAX_SECTOR_CODES = 200;
const CODE_RE = /^\d{6}$/;
const NAME_RE = /^[^\u0000-\u001f\u007f]{1,30}$/;

export interface CustomSectorRow {
  name: string;
  codes: string[];
  state: 'active' | 'deleted';
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
  rev: number;
}

export interface CustomSectorSnapshot { rows: CustomSectorRow[]; rev: number }
export interface ImportSector { name: string; codes: string[] }
export interface ImportSectorTombstone { name: string; deletedAt: string }

export interface CustomSectorStore {
  readonly kind: string;
  init(): Promise<void>;
  snapshot(): Promise<CustomSectorSnapshot>;
  /** 返回 'created' | 'updated' | 'unchanged' */
  put(name: string, codes: string[], now: Date, limit: number): Promise<'created' | 'updated' | 'unchanged'>;
  remove(name: string, now: Date): Promise<boolean>;
  importLocal(sectors: ImportSector[], tombstones: ImportSectorTombstone[], now: Date): Promise<{ added: number; deleted: number }>;
  close?(): Promise<void>;
}

const iso = (d: Date | string) => (d instanceof Date ? d : new Date(d)).toISOString();
const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));
const sameCodes = (a: string[], b: string[]) => a.length === b.length && a.every((c, i) => c === b[i]);

export function normalizeName(name: unknown): string {
  const n = String(name ?? '').trim();
  if (!NAME_RE.test(n)) throw new WatchlistInputError('板块名称需为 1–30 个字符，且不含控制字符');
  return n;
}

export function normalizeCodes(codes: unknown): string[] {
  if (!Array.isArray(codes)) throw new WatchlistInputError('codes 必须是股票代码数组');
  const out: string[] = [];
  const bad: string[] = [];
  for (const c of codes) {
    const s = String(c ?? '').trim();
    if (!CODE_RE.test(s)) bad.push(s);
    else if (!out.includes(s)) out.push(s);
  }
  if (bad.length) throw new WatchlistInputError(`股票代码必须为 6 位数字：${bad.slice(0, 5).join(', ')}`);
  if (!out.length) throw new WatchlistInputError('板块至少要有 1 只股票');
  if (out.length > MAX_SECTOR_CODES) throw new WatchlistInputError(`单个自定义板块最多 ${MAX_SECTOR_CODES} 只`);
  return out;
}

// ------------------------------------------------------------------ 内存实现（测试 / 显式本地开发）

export class MemoryCustomSectorStore implements CustomSectorStore {
  readonly kind: string = 'memory';
  private readonly data = new Map<string, CustomSectorRow>();
  private rev = 0;

  async init(): Promise<void> {}

  async snapshot(): Promise<CustomSectorSnapshot> {
    return { rows: [...this.data.values()].map((r) => ({ ...r, codes: r.codes.slice() })), rev: this.rev };
  }

  private write<T>(fn: (put: (row: Omit<CustomSectorRow, 'rev'>) => void) => T): T {
    const next = this.rev + 1;
    let changed = 0;
    const out = fn((row) => { this.data.set(row.name, { ...row, codes: row.codes.slice(), rev: next }); changed++; });
    if (changed > 0) this.rev = next;
    return out;
  }

  async put(name: string, codes: string[], now: Date, limit: number) {
    return this.write((put) => {
      const r = this.data.get(name);
      if (r && r.state === 'active' && sameCodes(r.codes, codes)) return 'unchanged' as const;
      if (!(r && r.state === 'active') && [...this.data.values()].filter((x) => x.state === 'active').length >= limit) {
        throw new WatchlistInputError(`自定义板块最多 ${limit} 个`);
      }
      const created = r && r.state === 'active' ? r.created_at : iso(now);
      put({ name, codes, state: 'active', created_at: created, updated_at: iso(now), deleted_at: null });
      return r && r.state === 'active' ? 'updated' as const : 'created' as const;
    });
  }

  async remove(name: string, now: Date) {
    return this.write((put) => {
      const r = this.data.get(name);
      if (r && r.state === 'deleted') return false;
      put({ name, codes: r ? r.codes : [], state: 'deleted', created_at: r ? r.created_at : iso(now), updated_at: iso(now), deleted_at: iso(now) });
      return true;
    });
  }

  async importLocal(sectors: ImportSector[], tombstones: ImportSectorTombstone[], now: Date) {
    return this.write((put) => {
      let added = 0, deleted = 0;
      for (const t of tombstones) {
        const r = this.data.get(t.name);
        const at = iso(t.deletedAt);
        if (!r) { put({ name: t.name, codes: [], state: 'deleted', created_at: at, updated_at: at, deleted_at: at }); deleted++; }
        else if (r.updated_at < at && r.state === 'active') { put({ ...r, state: 'deleted', updated_at: at, deleted_at: at }); deleted++; }
      }
      sectors.forEach((s, i) => {
        if (!this.data.has(s.name)) {
          const at = iso(new Date(now.getTime() + i));
          put({ name: s.name, codes: s.codes, state: 'active', created_at: at, updated_at: at, deleted_at: null });
          added++;
        }
      });
      return { added, deleted };
    });
  }
}

// ------------------------------------------------------------------ PostgreSQL 实现（生产）

type Sql = any;
const PG_CLIENT_OPTIONS = { max: 2, idle_timeout: 30, connect_timeout: 10, onnotice: () => {} };

/** 建表脚本：由平台 migration 执行（仓库 custom-sectors.migration.sql 与之一致，测试校验）；可重复执行 */
export function customSectorsMigrationSql(table = CS_TABLE): string {
  if (!/^[a-z_][a-z0-9_]*$/.test(table)) throw new Error(`非法表名：${table}`);
  return `-- FIR-41 第 4 项：自定义板块（全站共用一份）。由平台 migration 执行；应用运行时只做 DML，不建表。
-- 应用账号需要：两张表的 SELECT, INSERT, UPDATE（不需要 DELETE / CREATE）。可重复执行。
CREATE TABLE IF NOT EXISTS ${table} (
  name        text PRIMARY KEY CHECK (char_length(name) BETWEEN 1 AND 30),
  codes_json  text NOT NULL,
  state       text NOT NULL CHECK (state IN ('active', 'deleted')),
  created_at  timestamptz NOT NULL,
  updated_at  timestamptz NOT NULL,
  deleted_at  timestamptz,
  rev         bigint NOT NULL
);
CREATE TABLE IF NOT EXISTS ${table}_meta (
  id   int PRIMARY KEY CHECK (id = 1),
  rev  bigint NOT NULL
);
-- 版本行缺失时按已有数据的最大 rev 补上（空表为 0），不覆盖、不降低已存在的版本
INSERT INTO ${table}_meta (id, rev)
SELECT 1, COALESCE(MAX(rev), 0) FROM ${table}
ON CONFLICT (id) DO NOTHING;
`;
}

/** 测试 / 本地开发用：用有建表权限的连接执行建表脚本。生产不调用。 */
export async function applyCustomSectorsMigration(sql: Sql, table = CS_TABLE): Promise<void> {
  await sql.unsafe(customSectorsMigrationSql(table));
}

class NoChange extends Error {
  constructor(readonly out: unknown) { super('no change'); }
}

function describePgError(err: unknown, table: string): string {
  const code = (err as any)?.code;
  const msg = errMsg(err);
  if (code === '42P01') return `自定义板块数据表不存在（${table} / ${table}_meta）：应用运行时不建表，需先由平台执行 custom-sectors.migration.sql（${msg}）`;
  if (code === '42501') return `应用账号缺少自定义板块表的读写权限（需要 SELECT / INSERT / UPDATE）（${msg}）`;
  return msg;
}

export class PostgresCustomSectorStore implements CustomSectorStore {
  readonly kind = 'postgres';
  private readonly meta: string;

  constructor(private readonly sql: Sql, private readonly table = CS_TABLE) {
    if (!/^[a-z_][a-z0-9_]*$/.test(table)) throw new Error(`非法表名：${table}`);
    this.meta = `${table}_meta`;
  }

  private t(q: Sql = this.sql) { return q(this.table); }
  private m(q: Sql = this.sql) { return q(this.meta); }

  async init(): Promise<void> {
    try {
      const [meta] = await this.sql`SELECT rev FROM ${this.m()} WHERE id = 1`;
      if (!meta) {
        await this.sql`
          INSERT INTO ${this.m()} (id, rev)
          SELECT 1, COALESCE(MAX(rev), 0) FROM ${this.t()}
          ON CONFLICT (id) DO NOTHING`;
      }
      await this.snapshot();
    } catch (err) {
      throw new Error(describePgError(err, this.table));
    }
  }

  async snapshot(): Promise<CustomSectorSnapshot> {
    const rs = await this.sql`
      SELECT m.rev AS meta_rev, c.name, c.codes_json, c.state, c.created_at, c.updated_at, c.deleted_at, c.rev
      FROM ${this.m()} m LEFT JOIN ${this.t()} c ON true
      WHERE m.id = 1`;
    if (!rs.length) throw new Error('自定义板块版本记录缺失（未初始化）');
    const rows: CustomSectorRow[] = rs.filter((r: any) => r.name !== null).map((r: any) => ({
      name: r.name, codes: JSON.parse(r.codes_json), state: r.state,
      created_at: iso(r.created_at), updated_at: iso(r.updated_at), deleted_at: r.deleted_at ? iso(r.deleted_at) : null,
      rev: Number(r.rev),
    }));
    return { rows, rev: Number(rs[0].meta_rev) };
  }

  private async write<T>(fn: (tx: Sql, rev: number) => Promise<{ out: T; changed: number }>): Promise<T> {
    try {
      return await this.sql.begin(async (tx: Sql) => {
        const [meta] = await tx`UPDATE ${this.m(tx)} SET rev = rev + 1 WHERE id = 1 RETURNING rev`;
        if (!meta) throw new Error('自定义板块版本记录缺失（未初始化）');
        const { out, changed } = await fn(tx, Number(meta.rev));
        if (changed === 0) throw new NoChange(out);
        return out;
      });
    } catch (err) {
      if (err instanceof NoChange) return err.out as T;
      throw err;
    }
  }

  async put(name: string, codes: string[], now: Date, limit: number) {
    return this.write(async (tx, rev) => {
      const [cur] = await tx`SELECT state, codes_json, created_at FROM ${this.t(tx)} WHERE name = ${name}`;
      const active = cur && cur.state === 'active';
      if (active && sameCodes(JSON.parse(cur.codes_json), codes)) return { out: 'unchanged' as const, changed: 0 };
      if (!active) {
        const [{ n }] = await tx`SELECT count(*)::int AS n FROM ${this.t(tx)} WHERE state = 'active'`;
        if (n >= limit) throw new WatchlistInputError(`自定义板块最多 ${limit} 个`);
      }
      const created = active ? cur.created_at : now;
      await tx`
        INSERT INTO ${this.t(tx)} (name, codes_json, state, created_at, updated_at, deleted_at, rev)
        VALUES (${name}, ${JSON.stringify(codes)}, 'active', ${created}, ${now}, NULL, ${rev})
        ON CONFLICT (name) DO UPDATE
          SET codes_json = EXCLUDED.codes_json, state = 'active', created_at = EXCLUDED.created_at,
              updated_at = EXCLUDED.updated_at, deleted_at = NULL, rev = EXCLUDED.rev`;
      return { out: active ? 'updated' as const : 'created' as const, changed: 1 };
    });
  }

  async remove(name: string, now: Date) {
    return this.write(async (tx, rev) => {
      const rs = await tx`
        INSERT INTO ${this.t(tx)} (name, codes_json, state, created_at, updated_at, deleted_at, rev)
        VALUES (${name}, '[]', 'deleted', ${now}, ${now}, ${now}, ${rev})
        ON CONFLICT (name) DO UPDATE
          SET state = 'deleted', updated_at = EXCLUDED.updated_at, deleted_at = EXCLUDED.deleted_at, rev = EXCLUDED.rev
          WHERE ${this.t(tx)}.state <> 'deleted'
        RETURNING name`;
      return { out: rs.length > 0, changed: rs.length };
    });
  }

  async importLocal(sectors: ImportSector[], tombstones: ImportSectorTombstone[], now: Date) {
    return this.write(async (tx, rev) => {
      let added = 0, deleted = 0;
      for (const t of tombstones) {
        const at = new Date(t.deletedAt);
        const rs = await tx`
          INSERT INTO ${this.t(tx)} (name, codes_json, state, created_at, updated_at, deleted_at, rev)
          VALUES (${t.name}, '[]', 'deleted', ${at}, ${at}, ${at}, ${rev})
          ON CONFLICT (name) DO UPDATE
            SET state = 'deleted', updated_at = EXCLUDED.updated_at, deleted_at = EXCLUDED.deleted_at, rev = EXCLUDED.rev
            WHERE ${this.t(tx)}.state = 'active' AND ${this.t(tx)}.updated_at < EXCLUDED.updated_at
          RETURNING name`;
        deleted += rs.length;
      }
      for (const [i, s] of sectors.entries()) {
        const at = new Date(now.getTime() + i);
        const rs = await tx`
          INSERT INTO ${this.t(tx)} (name, codes_json, state, created_at, updated_at, deleted_at, rev)
          VALUES (${s.name}, ${JSON.stringify(s.codes)}, 'active', ${at}, ${at}, NULL, ${rev})
          ON CONFLICT (name) DO NOTHING
          RETURNING name`;
        added += rs.length;
      }
      return { out: { added, deleted }, changed: added + deleted };
    });
  }

  async close(): Promise<void> {
    await this.sql.end({ timeout: 5 });
  }
}

export class UnavailableCustomSectorStore implements CustomSectorStore {
  readonly kind = 'unavailable';
  constructor(private readonly reason: string) {}
  private fail(): never { throw new WatchlistUnavailableError(this.reason); }
  async init(): Promise<void> { return this.fail(); }
  async snapshot(): Promise<CustomSectorSnapshot> { return this.fail(); }
  async put(): Promise<'created'> { return this.fail(); }
  async remove(): Promise<boolean> { return this.fail(); }
  async importLocal(): Promise<{ added: number; deleted: number }> { return this.fail(); }
}

/** 与 createWatchlistStore 同一选择规则；驱动缺失 / 连接串错误只让自定义板块 fail-closed，原因里去掉连接串 */
export function createCustomSectorStore(
  env: Record<string, string | undefined>,
  loadDriver: () => any = () => require('postgres'),
): CustomSectorStore {
  if (env.WATCHLIST_STORE === 'memory') return new MemoryCustomSectorStore();
  const url = env.WATCHLIST_DATABASE_URL || env.SUDA_DATABASE_URL;
  if (!url) return new UnavailableCustomSectorStore('自定义板块存储未配置（缺少 SUDA_DATABASE_URL），不在浏览器本地兜底保存');
  try {
    const postgres = loadDriver();
    return new PostgresCustomSectorStore(postgres(url, PG_CLIENT_OPTIONS));
  } catch (err) {
    return new UnavailableCustomSectorStore(`自定义板块存储不可用：PostgreSQL 驱动加载或连接串解析失败（${errMsg(err).split(url).join('***')}）`);
  }
}

// ------------------------------------------------------------------ 对外接口层（校验 + 视图 + 就绪状态）

export interface CustomSectorView {
  items: Array<{ name: string; codes: string[]; count: number; updated_at: string }>;
  tombstones: Array<{ name: string; deleted_at: string }>;
  count: number;
  rev: number;
  storage: string;
}

export interface CustomSectorStatus { kind: string; ready: boolean; error: string | null; checked_at: string | null }

export class CustomSectors {
  private ready = false;
  private lastError: string | null = null;
  private checkedAt: string | null = null;

  constructor(private readonly store: CustomSectorStore, private readonly now: () => Date = () => new Date()) {}

  async init(): Promise<void> {
    try { await this.store.init(); this.markOk(); } catch (err) { this.markDown(err); }
  }

  status(): CustomSectorStatus {
    return { kind: this.store.kind, ready: this.ready, error: this.lastError, checked_at: this.checkedAt };
  }

  async check(): Promise<CustomSectorStatus> {
    try { await this.view(); } catch { /* 状态已在 run() 中更新 */ }
    return this.status();
  }

  private markOk(): void { this.ready = true; this.lastError = null; this.checkedAt = this.now().toISOString(); }
  private markDown(err: unknown): void { this.ready = false; this.lastError = errMsg(err); this.checkedAt = this.now().toISOString(); }

  private async run<T>(fn: () => Promise<T>): Promise<T> {
    if (!this.ready) await this.init();
    if (!this.ready) throw new WatchlistUnavailableError(`自定义板块存储不可用：${this.lastError}`);
    try {
      const out = await fn();
      this.markOk();
      return out;
    } catch (err) {
      if (err instanceof WatchlistInputError) throw err;
      this.markDown(err);
      throw new WatchlistUnavailableError(`自定义板块存储不可用：${this.lastError}`);
    }
  }

  private async buildView(): Promise<CustomSectorView> {
    const { rows, rev } = await this.store.snapshot();
    const active = rows.filter((r) => r.state === 'active')
      .sort((a, b) => (a.created_at < b.created_at ? -1 : a.created_at > b.created_at ? 1 : a.name < b.name ? -1 : 1));
    const tomb = rows.filter((r) => r.state === 'deleted').sort((a, b) => (a.name < b.name ? -1 : 1))
      .map((r) => ({ name: r.name, deleted_at: r.deleted_at as string }));
    return {
      items: active.map((r) => ({ name: r.name, codes: r.codes, count: r.codes.length, updated_at: r.updated_at })),
      tombstones: tomb, count: active.length, rev, storage: this.store.kind,
    };
  }

  view(): Promise<CustomSectorView> { return this.run(() => this.buildView()); }

  async put(name: unknown, body: unknown): Promise<CustomSectorView & { result: string }> {
    const n = normalizeName(name);
    const codes = normalizeCodes((body as any)?.codes);
    return this.run(async () => {
      const result = await this.store.put(n, codes, this.now(), MAX_CUSTOM_SECTORS);
      return { ...(await this.buildView()), result };
    });
  }

  async remove(name: unknown): Promise<CustomSectorView & { removed: boolean }> {
    const n = normalizeName(name);
    return this.run(async () => {
      const removed = await this.store.remove(n, this.now());
      return { ...(await this.buildView()), removed };
    });
  }

  /** 浏览器一次性迁入：sectors 为本机 {名称: 代码[]}（或 [{name, codes}]），tombstones 为 [{name, deletedAt}] */
  async importLocal(body: unknown): Promise<CustomSectorView & { added: number; deleted: number; skipped: string[] }> {
    const b = (body ?? {}) as { sectors?: unknown; tombstones?: unknown };
    const skipped: string[] = [];
    const list: Array<{ name: unknown; codes: unknown }> = Array.isArray(b.sectors)
      ? (b.sectors as any[]).map((s) => ({ name: s?.name, codes: s?.codes }))
      : (b.sectors && typeof b.sectors === 'object' ? Object.entries(b.sectors as object).map(([name, codes]) => ({ name, codes })) : []);
    const sectors: ImportSector[] = [];
    for (const s of list) {
      try {
        const name = normalizeName(s.name);
        // 迁入时只保留合法代码（旧浏览器数据可能混有错误代码），一只都不剩就跳过
        const codes = normalizeCodes(Array.isArray(s.codes) ? (s.codes as unknown[]).filter((c) => CODE_RE.test(String(c ?? '').trim())) : s.codes);
        if (!sectors.some((x) => x.name === name)) sectors.push({ name, codes });
      } catch {
        skipped.push(String(s.name ?? '(无名称)'));
      }
    }
    const tombs: ImportSectorTombstone[] = [];
    for (const t of Array.isArray(b.tombstones) ? b.tombstones : []) {
      const at = new Date(String((t as any)?.deletedAt ?? (t as any)?.deleted_at ?? ''));
      let name: string;
      try { name = normalizeName((t as any)?.name); } catch { skipped.push('(无效删除记录)'); continue; }
      if (Number.isNaN(at.getTime())) { skipped.push(name); continue; }
      const now = this.now();
      tombs.push({ name, deletedAt: (at > now ? now : at).toISOString() });
    }
    if (sectors.length > MAX_CUSTOM_SECTORS || tombs.length > 1000) throw new WatchlistInputError('迁入数据过多，请检查本机数据');
    return this.run(async () => {
      const r = await this.store.importLocal(sectors, tombs, this.now());
      return { ...(await this.buildView()), ...r, skipped };
    });
  }
}
