/**
 * FIR-41 第 2/3 项：自选股存到后端（全站共用一份清单），手机 / 电脑多端同步，删除不复活。
 *
 * - 一只股票一行：active（在清单里）/ deleted（删除记录，防止旧设备迁入时复活）。
 * - 生产用 PostgreSQL（妙搭托管，连接串 SUDA_DATABASE_URL）；测试 / 本地可显式用内存实现。
 *   两种实现遵守同一套规则，tests/v6/test_watchlist_store.js 用同一组用例分别验证。
 * - 本模块无 Nest 依赖；postgres 驱动（postgres-js）在运行时按需加载。
 *
 * 规则：
 * - add：变为 active（已在清单里则不改动，返回 added=false）；用户主动加回会清除删除记录。
 * - remove：变为 deleted（即使从没加过也写删除记录，之后的迁入不会把它带回来）。
 * - clear：所有 active 变为 deleted。
 * - importLocal（浏览器一次性迁入）：先应用删除记录（只覆盖比它更早的服务器记录），
 *   再加入代码（只加入服务器上完全没有记录的代码，绝不覆盖已有 active / deleted；保持本机原有顺序）。
 *
 * 版本号 rev（QA R73-2）：每次写入都给被改动的行分配一个全局递增的序号（PostgreSQL 用 SEQUENCE，跨实例单调），
 * 行从不物理删除，所以清单的 rev = max(行序号) 单调不减。读取在单条 SQL 内完成（同一快照）。
 * 前端只接受 rev 不小于当前值的响应，迟到的旧读取不会覆盖已完成的更新。
 *
 * 就绪状态（QA R73-3）：未配置 / 初始化失败 / 运行中存储出错 → ready=false 并返回 503；
 * 下一次调用自动重新初始化，成功后恢复 ready=true。check() 做一次真实读取，供 /api/health 使用。
 */

export type RowState = 'active' | 'deleted';

export interface WatchlistRow {
  code: string;
  state: RowState;
  added_at: string;          // ISO
  deleted_at: string | null; // ISO
  updated_at: string;        // ISO
  rev: number;               // 最近一次写入该行时分配的全局序号
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

function errMsg(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

// ------------------------------------------------------------------ 内存实现（测试 / 显式本地开发）

export class MemoryWatchlistStore implements WatchlistStore {
  readonly kind: string = 'memory';
  private readonly data = new Map<string, WatchlistRow>();
  private seq = 0;

  async init(): Promise<void> {}

  async rows(): Promise<WatchlistRow[]> {
    return [...this.data.values()].map((r) => ({ ...r }));
  }

  private put(row: Omit<WatchlistRow, 'rev'>): void {
    this.data.set(row.code, { ...row, rev: ++this.seq });
  }

  async add(code: string, now: Date): Promise<boolean> {
    const r = this.data.get(code);
    if (r && r.state === 'active') return false;
    this.put({ code, state: 'active', added_at: iso(now), deleted_at: null, updated_at: iso(now) });
    return true;
  }

  async remove(code: string, now: Date): Promise<void> {
    const r = this.data.get(code);
    this.put({ code, state: 'deleted', added_at: r ? r.added_at : iso(now), deleted_at: iso(now), updated_at: iso(now) });
  }

  async clear(now: Date): Promise<number> {
    let n = 0;
    for (const r of [...this.data.values()]) {
      if (r.state === 'active') {
        this.put({ ...r, state: 'deleted', deleted_at: iso(now), updated_at: iso(now) });
        n++;
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
        this.put({ code: t.code, state: 'deleted', added_at: at, deleted_at: at, updated_at: at });
        deleted++;
      } else if (r.updated_at < at) {
        this.put({ ...r, state: 'deleted', deleted_at: at, updated_at: at });
        deleted++;
      }
    }
    codes.forEach((code, i) => {
      if (!this.data.has(code)) {
        const at = new Date(now.getTime() + i);   // 每只相差 1ms，保持本机原有顺序
        this.put({ code, state: 'active', added_at: iso(at), deleted_at: null, updated_at: iso(at) });
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
  private readonly seq: string;

  constructor(urlOrSql: string | Sql, private readonly table = PG_TABLE) {
    if (!/^[a-z_][a-z0-9_]*$/.test(table)) throw new Error(`非法表名：${table}`);
    this.seq = `${table}_rev_seq`;
    if (typeof urlOrSql === 'string') {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const postgres = require('postgres');
      this.sql = postgres(urlOrSql, { max: 3, idle_timeout: 30, connect_timeout: 10, onnotice: () => {} });
    } else {
      this.sql = urlOrSql;
    }
  }

  private t(q: Sql = this.sql) {
    return q(this.table);
  }

  /** nextval 片段（序列名作为参数传入并转换为 regclass，不拼接 SQL） */
  private next(q: Sql = this.sql) {
    return q`nextval(${this.seq}::regclass)`;
  }

  async init(): Promise<void> {
    await this.sql`CREATE SEQUENCE IF NOT EXISTS ${this.sql(this.seq)}`;
    await this.sql`
      CREATE TABLE IF NOT EXISTS ${this.t()} (
        code        text PRIMARY KEY CHECK (code ~ '^[0-9]{6}$'),
        state       text NOT NULL CHECK (state IN ('active', 'deleted')),
        added_at    timestamptz NOT NULL,
        deleted_at  timestamptz,
        updated_at  timestamptz NOT NULL,
        rev         bigint NOT NULL
      )`;
  }

  async rows(): Promise<WatchlistRow[]> {
    // 单条 SQL：同一快照
    const rs = await this.sql`SELECT code, state, added_at, deleted_at, updated_at, rev FROM ${this.t()}`;
    return rs.map((r: any) => ({
      code: r.code, state: r.state,
      added_at: iso(r.added_at), deleted_at: r.deleted_at ? iso(r.deleted_at) : null, updated_at: iso(r.updated_at),
      rev: Number(r.rev),
    }));
  }

  async add(code: string, now: Date): Promise<boolean> {
    // 已 active 的不改动（WHERE 不满足 → 无返回行、不消耗序号）
    const rs = await this.sql`
      INSERT INTO ${this.t()} (code, state, added_at, deleted_at, updated_at, rev)
      VALUES (${code}, 'active', ${now}, NULL, ${now}, ${this.next()})
      ON CONFLICT (code) DO UPDATE
        SET state = 'active', added_at = EXCLUDED.added_at, deleted_at = NULL,
            updated_at = EXCLUDED.updated_at, rev = EXCLUDED.rev
        WHERE ${this.t()}.state <> 'active'
      RETURNING code`;
    return rs.length > 0;
  }

  async remove(code: string, now: Date): Promise<void> {
    await this.sql`
      INSERT INTO ${this.t()} (code, state, added_at, deleted_at, updated_at, rev)
      VALUES (${code}, 'deleted', ${now}, ${now}, ${now}, ${this.next()})
      ON CONFLICT (code) DO UPDATE
        SET state = 'deleted', deleted_at = EXCLUDED.deleted_at, updated_at = EXCLUDED.updated_at, rev = EXCLUDED.rev`;
  }

  async clear(now: Date): Promise<number> {
    const rs = await this.sql`
      UPDATE ${this.t()} SET state = 'deleted', deleted_at = ${now}, updated_at = ${now}, rev = ${this.next()}
      WHERE state = 'active' RETURNING code`;
    return rs.length;
  }

  async importLocal(codes: string[], tombstones: ImportTombstone[], now: Date): Promise<{ added: number; deleted: number }> {
    return this.sql.begin(async (tx: Sql) => {
      let added = 0, deleted = 0;
      for (const t of tombstones) {
        const at = new Date(t.deletedAt);
        const rs = await tx`
          INSERT INTO ${this.t(tx)} (code, state, added_at, deleted_at, updated_at, rev)
          VALUES (${t.code}, 'deleted', ${at}, ${at}, ${at}, ${this.next(tx)})
          ON CONFLICT (code) DO UPDATE
            SET state = 'deleted', deleted_at = EXCLUDED.deleted_at, updated_at = EXCLUDED.updated_at, rev = EXCLUDED.rev
            WHERE ${this.t(tx)}.updated_at < EXCLUDED.updated_at
          RETURNING code`;
        deleted += rs.length;
      }
      for (const [i, code] of codes.entries()) {
        const at = new Date(now.getTime() + i);   // 每只相差 1ms，保持本机原有顺序
        const rs = await tx`
          INSERT INTO ${this.t(tx)} (code, state, added_at, deleted_at, updated_at, rev)
          VALUES (${code}, 'active', ${at}, NULL, ${at}, ${this.next(tx)})
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
  /** 未配置时初始化即失败 → ready=false（QA R73-3） */
  async init(): Promise<void> { return this.fail(); }
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

// ------------------------------------------------------------------ 对外接口层（校验 + 视图 + 就绪状态），与实现无关

export interface WatchlistView {
  items: string[];
  tombstones: Array<{ code: string; deleted_at: string }>;
  count: number;
  rev: number;            // 单调不减；空库为 0
  storage: string;
}

export interface WatchlistStatus {
  kind: string;
  ready: boolean;
  error: string | null;
  checked_at: string | null;
}

export class Watchlist {
  private ready = false;
  private lastError: string | null = null;
  private checkedAt: string | null = null;

  constructor(private readonly store: WatchlistStore, private readonly now: () => Date = () => new Date()) {}

  get kind(): string {
    return this.store.kind;
  }

  async init(): Promise<void> {
    try {
      await this.store.init();
      this.markOk();
    } catch (err) {
      this.markDown(err);
    }
  }

  status(): WatchlistStatus {
    return { kind: this.store.kind, ready: this.ready, error: this.lastError, checked_at: this.checkedAt };
  }

  /** 真实读取一次（/api/health 用）：反映当前是否真的可用，而不是启动时的结果 */
  async check(): Promise<WatchlistStatus> {
    try {
      await this.view();
    } catch {
      // 状态已在 run() 中更新
    }
    return this.status();
  }

  private markOk(): void {
    this.ready = true;
    this.lastError = null;
    this.checkedAt = this.now().toISOString();
  }

  private markDown(err: unknown): void {
    this.ready = false;
    this.lastError = errMsg(err);
    this.checkedAt = this.now().toISOString();
  }

  /** 统一执行存储操作：未就绪先重试初始化；存储层错误（连接断开等）→ 标记不可用并返回 503 */
  private async run<T>(fn: () => Promise<T>): Promise<T> {
    if (!this.ready) await this.init();
    if (!this.ready) throw new WatchlistUnavailableError(`自选股存储不可用：${this.lastError}`);
    try {
      const out = await fn();
      this.markOk();
      return out;
    } catch (err) {
      if (err instanceof WatchlistInputError) throw err;
      this.markDown(err);
      throw new WatchlistUnavailableError(`自选股存储不可用：${this.lastError}`);
    }
  }

  private async buildView(): Promise<WatchlistView> {
    const rows = await this.store.rows();
    const active = rows.filter((r) => r.state === 'active')
      .sort((a, b) => (a.added_at < b.added_at ? -1 : a.added_at > b.added_at ? 1 : a.code < b.code ? -1 : 1));
    const tomb = rows.filter((r) => r.state === 'deleted')
      .sort((a, b) => (a.code < b.code ? -1 : 1))
      .map((r) => ({ code: r.code, deleted_at: r.deleted_at as string }));
    const rev = rows.reduce((m, r) => Math.max(m, r.rev), 0);
    return { items: active.map((r) => r.code), tombstones: tomb, count: active.length, rev, storage: this.store.kind };
  }

  async view(): Promise<WatchlistView> {
    return this.run(() => this.buildView());
  }

  async codes(): Promise<string[]> {
    return (await this.view()).items;
  }

  async add(code: unknown): Promise<WatchlistView & { added: boolean }> {
    const c = assertCode(code);
    return this.run(async () => {
      const current = await this.buildView();
      if (!current.items.includes(c) && current.count >= MAX_WATCHLIST) {
        throw new WatchlistInputError(`自选股最多 ${MAX_WATCHLIST} 只`);
      }
      const added = await this.store.add(c, this.now());
      return { ...(await this.buildView()), added };
    });
  }

  async remove(code: unknown): Promise<WatchlistView> {
    const c = assertCode(code);
    return this.run(async () => {
      await this.store.remove(c, this.now());
      return this.buildView();
    });
  }

  async clear(): Promise<WatchlistView & { cleared: number }> {
    return this.run(async () => {
      const cleared = await this.store.clear(this.now());
      return { ...(await this.buildView()), cleared };
    });
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
    return this.run(async () => {
      const r = await this.store.importLocal(codes, tombs, this.now());
      return { ...(await this.buildView()), ...r, skipped };
    });
  }
}
