/**
 * FIR-41 第 2/3 项：自选股存到后端（全站共用一份清单），手机 / 电脑多端同步，删除不复活。
 *
 * - 一只股票一行：active（在清单里）/ deleted（删除记录，防止旧设备迁入时复活）。
 * - 生产用 PostgreSQL（妙搭托管，连接串 SUDA_DATABASE_URL）；测试 / 本地可显式用内存实现。
 *   两种实现遵守同一套规则，tests/v6/test_watchlist_store.js 用同一组用例分别验证。
 * - 本模块无 Nest 依赖；postgres 驱动（postgres-js）在运行时按需加载。
 *
 * 规则：
 * - add：变为 active（已在清单里则保持原加入时间，返回 added=false）；用户主动加回会清除删除记录。
 * - remove：变为 deleted（即使从没加过也写删除记录，之后的迁入不会把它带回来）。
 * - clear：所有 active 变为 deleted。
 * - importLocal（浏览器一次性迁入）：先应用删除记录（只覆盖比它更早的服务器记录），
 *   再加入代码（只加入服务器上完全没有记录的代码，绝不覆盖已有 active / deleted；保持本机原有顺序）。
 */

export type RowState = 'active' | 'deleted';

export interface WatchlistRow {
  code: string;
  state: RowState;
  added_at: string;          // ISO
  deleted_at: string | null; // ISO
  updated_at: string;        // ISO
}

export interface ImportTombstone {
  code: string;
  deletedAt: string;
}

export interface WatchlistStore {
  readonly kind: string;
  init(): Promise<void>;
  rows(): Promise<WatchlistRow[]>;
  /** 返回 true = 新加入（之前不在清单里） */
  add(code: string, now: Date): Promise<boolean>;
  remove(code: string, now: Date): Promise<void>;
  clear(now: Date): Promise<number>;
  importLocal(codes: string[], tombstones: ImportTombstone[], now: Date): Promise<{ added: number; deleted: number }>;
  close?(): Promise<void>;
}

export class WatchlistInputError extends Error {
  readonly statusCode = 400;
}

export class WatchlistUnavailableError extends Error {
  readonly statusCode = 503;
}

const CODE_RE = /^\d{6}$/;
export const MAX_WATCHLIST = 500;

function assertCode(code: unknown): string {
  const c = String(code ?? '').trim();
  if (!CODE_RE.test(c)) throw new WatchlistInputError(`股票代码必须为 6 位数字：${JSON.stringify(code)}`);
  return c;
}

function iso(d: Date | string): string {
  return (d instanceof Date ? d : new Date(d)).toISOString();
}

// ------------------------------------------------------------------ 内存实现（测试 / 显式本地开发）

export class MemoryWatchlistStore implements WatchlistStore {
  readonly kind = 'memory';
  private readonly data = new Map<string, WatchlistRow>();

  async init(): Promise<void> {}

  async rows(): Promise<WatchlistRow[]> {
    return [...this.data.values()].map((r) => ({ ...r }));
  }

  async add(code: string, now: Date): Promise<boolean> {
    const r = this.data.get(code);
    if (r && r.state === 'active') return false;
    this.data.set(code, { code, state: 'active', added_at: iso(now), deleted_at: null, updated_at: iso(now) });
    return true;
  }

  async remove(code: string, now: Date): Promise<void> {
    const r = this.data.get(code);
    this.data.set(code, {
      code, state: 'deleted', added_at: r ? r.added_at : iso(now), deleted_at: iso(now), updated_at: iso(now),
    });
  }

  async clear(now: Date): Promise<number> {
    let n = 0;
    for (const r of this.data.values()) {
      if (r.state === 'active') {
        r.state = 'deleted'; r.deleted_at = iso(now); r.updated_at = iso(now); n++;
      }
    }
    return n;
  }

  async importLocal(codes: string[], tombstones: ImportTombstone[], now: Date): Promise<{ added: number; deleted: number }> {
    let added = 0, deleted = 0;
    for (const t of tombstones) {
      const r = this.data.get(t.code);
      const at = iso(t.deletedAt);
      if (!r) {
        this.data.set(t.code, { code: t.code, state: 'deleted', added_at: at, deleted_at: at, updated_at: at });
        deleted++;
      } else if (r.updated_at < at) {
        r.state = 'deleted'; r.deleted_at = at; r.updated_at = at;
        deleted++;
      }
    }
    codes.forEach((code, i) => {
      if (!this.data.has(code)) {
        const at = new Date(now.getTime() + i);   // 每只相差 1ms，保持本机原有顺序
        this.data.set(code, { code, state: 'active', added_at: iso(at), deleted_at: null, updated_at: iso(at) });
        added++;
      }
    });
    return { added, deleted };
  }
}

// ------------------------------------------------------------------ PostgreSQL 实现（生产）

export const PG_TABLE = 'kline_watchlist';

type Sql = any; // postgres-js 实例（运行时加载，避免在无驱动的环境编译失败）

export class PostgresWatchlistStore implements WatchlistStore {
  readonly kind = 'postgres';
  private sql: Sql;

  constructor(urlOrSql: string | Sql, private readonly table = PG_TABLE) {
    if (!/^[a-z_][a-z0-9_]*$/.test(table)) throw new Error(`非法表名：${table}`);
    if (typeof urlOrSql === 'string') {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const postgres = require('postgres');
      this.sql = postgres(urlOrSql, { max: 3, idle_timeout: 30, connect_timeout: 10, onnotice: () => {} });
    } else {
      this.sql = urlOrSql;
    }
  }

  private t() {
    return this.sql(this.table);
  }

  async init(): Promise<void> {
    await this.sql`
      CREATE TABLE IF NOT EXISTS ${this.t()} (
        code        text PRIMARY KEY CHECK (code ~ '^[0-9]{6}$'),
        state       text NOT NULL CHECK (state IN ('active', 'deleted')),
        added_at    timestamptz NOT NULL,
        deleted_at  timestamptz,
        updated_at  timestamptz NOT NULL
      )`;
  }

  async rows(): Promise<WatchlistRow[]> {
    const rs = await this.sql`SELECT code, state, added_at, deleted_at, updated_at FROM ${this.t()}`;
    return rs.map((r: any) => ({
      code: r.code, state: r.state,
      added_at: iso(r.added_at), deleted_at: r.deleted_at ? iso(r.deleted_at) : null, updated_at: iso(r.updated_at),
    }));
  }

  async add(code: string, now: Date): Promise<boolean> {
    // RETURNING 中 (xmax = 0) 表示新插入；已 active 的不改动（WHERE 不满足 → 无返回行）
    const rs = await this.sql`
      INSERT INTO ${this.t()} (code, state, added_at, deleted_at, updated_at)
      VALUES (${code}, 'active', ${now}, NULL, ${now})
      ON CONFLICT (code) DO UPDATE
        SET state = 'active', added_at = EXCLUDED.added_at, deleted_at = NULL, updated_at = EXCLUDED.updated_at
        WHERE ${this.t()}.state <> 'active'
      RETURNING code`;
    return rs.length > 0;
  }

  async remove(code: string, now: Date): Promise<void> {
    await this.sql`
      INSERT INTO ${this.t()} (code, state, added_at, deleted_at, updated_at)
      VALUES (${code}, 'deleted', ${now}, ${now}, ${now})
      ON CONFLICT (code) DO UPDATE
        SET state = 'deleted', deleted_at = EXCLUDED.deleted_at, updated_at = EXCLUDED.updated_at`;
  }

  async clear(now: Date): Promise<number> {
    const rs = await this.sql`
      UPDATE ${this.t()} SET state = 'deleted', deleted_at = ${now}, updated_at = ${now}
      WHERE state = 'active' RETURNING code`;
    return rs.length;
  }

  async importLocal(codes: string[], tombstones: ImportTombstone[], now: Date): Promise<{ added: number; deleted: number }> {
    return this.sql.begin(async (tx: Sql) => {
      let added = 0, deleted = 0;
      for (const t of tombstones) {
        const at = new Date(t.deletedAt);
        const rs = await tx`
          INSERT INTO ${tx(this.table)} (code, state, added_at, deleted_at, updated_at)
          VALUES (${t.code}, 'deleted', ${at}, ${at}, ${at})
          ON CONFLICT (code) DO UPDATE
            SET state = 'deleted', deleted_at = EXCLUDED.deleted_at, updated_at = EXCLUDED.updated_at
            WHERE ${tx(this.table)}.updated_at < EXCLUDED.updated_at
          RETURNING code`;
        deleted += rs.length;
      }
      for (const [i, code] of codes.entries()) {
        const at = new Date(now.getTime() + i);   // 每只相差 1ms，保持本机原有顺序
        const rs = await tx`
          INSERT INTO ${tx(this.table)} (code, state, added_at, deleted_at, updated_at)
          VALUES (${code}, 'active', ${at}, NULL, ${at})
          ON CONFLICT (code) DO NOTHING
          RETURNING code`;
        added += rs.length;
      }
      return { added, deleted };
    });
  }

  async close(): Promise<void> {
    await this.sql.end({ timeout: 5 });
  }
}

// ------------------------------------------------------------------ 未配置（fail-closed）

export class UnavailableWatchlistStore implements WatchlistStore {
  readonly kind = 'unavailable';
  constructor(private readonly reason: string) {}
  private fail(): never {
    throw new WatchlistUnavailableError(this.reason);
  }
  async init(): Promise<void> {}
  async rows(): Promise<WatchlistRow[]> { return this.fail(); }
  async add(): Promise<boolean> { return this.fail(); }
  async remove(): Promise<void> { return this.fail(); }
  async clear(): Promise<number> { return this.fail(); }
  async importLocal(): Promise<{ added: number; deleted: number }> { return this.fail(); }
}

/** 按环境变量选择实现：WATCHLIST_STORE=memory（仅本地开发）；否则用 WATCHLIST_DATABASE_URL / SUDA_DATABASE_URL */
export function createWatchlistStore(env: Record<string, string | undefined>): WatchlistStore {
  if (env.WATCHLIST_STORE === 'memory') return new MemoryWatchlistStore();
  const url = env.WATCHLIST_DATABASE_URL || env.SUDA_DATABASE_URL;
  if (!url) {
    return new UnavailableWatchlistStore('自选股存储未配置（缺少 SUDA_DATABASE_URL），不在浏览器本地兜底保存');
  }
  return new PostgresWatchlistStore(url);
}

// ------------------------------------------------------------------ 对外接口层（校验 + 视图），与实现无关

export interface WatchlistView {
  items: string[];
  tombstones: Array<{ code: string; deleted_at: string }>;
  count: number;
  rev: string | null;
  storage: string;
}

export class Watchlist {
  private ready = false;
  private initError: string | null = null;

  constructor(private readonly store: WatchlistStore, private readonly now: () => Date = () => new Date()) {}

  get kind(): string {
    return this.store.kind;
  }

  async init(): Promise<void> {
    try {
      await this.store.init();
      this.ready = true;
      this.initError = null;
    } catch (err) {
      this.ready = false;
      this.initError = err instanceof Error ? err.message : String(err);
    }
  }

  status(): { kind: string; ready: boolean; error: string | null } {
    return { kind: this.store.kind, ready: this.ready, error: this.initError };
  }

  private async ensure(): Promise<void> {
    if (!this.ready) await this.init();
    if (!this.ready) throw new WatchlistUnavailableError(`自选股存储不可用：${this.initError}`);
  }

  async view(): Promise<WatchlistView> {
    await this.ensure();
    const rows = await this.store.rows();
    const active = rows.filter((r) => r.state === 'active')
      .sort((a, b) => (a.added_at < b.added_at ? -1 : a.added_at > b.added_at ? 1 : a.code < b.code ? -1 : 1));
    const tomb = rows.filter((r) => r.state === 'deleted')
      .sort((a, b) => (a.code < b.code ? -1 : 1))
      .map((r) => ({ code: r.code, deleted_at: r.deleted_at as string }));
    const rev = rows.reduce<string | null>((m, r) => (m === null || r.updated_at > m ? r.updated_at : m), null);
    return { items: active.map((r) => r.code), tombstones: tomb, count: active.length, rev, storage: this.store.kind };
  }

  async codes(): Promise<string[]> {
    return (await this.view()).items;
  }

  async add(code: unknown): Promise<WatchlistView & { added: boolean }> {
    const c = assertCode(code);
    await this.ensure();
    const current = await this.view();
    if (!current.items.includes(c) && current.count >= MAX_WATCHLIST) {
      throw new WatchlistInputError(`自选股最多 ${MAX_WATCHLIST} 只`);
    }
    const added = await this.store.add(c, this.now());
    return { ...(await this.view()), added };
  }

  async remove(code: unknown): Promise<WatchlistView> {
    const c = assertCode(code);
    await this.ensure();
    await this.store.remove(c, this.now());
    return this.view();
  }

  async clear(): Promise<WatchlistView & { cleared: number }> {
    await this.ensure();
    const cleared = await this.store.clear(this.now());
    return { ...(await this.view()), cleared };
  }

  /** 浏览器一次性迁入：codes 为本机清单，tombstones 为本机删除记录 [{code, deletedAt}] */
  async importLocal(body: unknown): Promise<WatchlistView & { added: number; deleted: number; skipped: string[] }> {
    const b = (body ?? {}) as { codes?: unknown; tombstones?: unknown };
    const skipped: string[] = [];
    const codes: string[] = [];
    for (const c of Array.isArray(b.codes) ? b.codes : []) {
      const s = String(c ?? '').trim();
      if (CODE_RE.test(s)) { if (!codes.includes(s)) codes.push(s); } else skipped.push(s);
    }
    const tombs: ImportTombstone[] = [];
    for (const t of Array.isArray(b.tombstones) ? b.tombstones : []) {
      const code = String((t as any)?.code ?? '').trim();
      const at = new Date(String((t as any)?.deletedAt ?? (t as any)?.deleted_at ?? ''));
      if (!CODE_RE.test(code) || Number.isNaN(at.getTime())) { skipped.push(code || '(无效删除记录)'); continue; }
      // 未来时间的删除记录（设备时钟超前）按现在处理，避免永远压住之后的加入
      const now = this.now();
      tombs.push({ code, deletedAt: (at > now ? now : at).toISOString() });
    }
    if (codes.length > MAX_WATCHLIST || tombs.length > 5000) {
      throw new WatchlistInputError('迁入数据过多，请检查本机数据');
    }
    await this.ensure();
    const r = await this.store.importLocal(codes, tombs, this.now());
    return { ...(await this.view()), ...r, skipped };
  }
}
