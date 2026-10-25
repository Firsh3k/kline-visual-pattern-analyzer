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
 * 版本号 rev（QA R73-2）：整份清单一个版本号，每个有实际改动的写操作 +1。PostgreSQL 用单行版本表加行锁，
 * 所有写操作串行、版本与数据同一事务提交；读取一条 SQL 同时取版本与数据（同一快照）。详见 PostgresWatchlistStore。
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
  rev: number;               // 最近一次写入该行时的清单版本
}

/** 一次一致读取：rows 与 rev 属于同一个已提交状态 */
export interface WatchlistSnapshot {
  rows: WatchlistRow[];
  rev: number;
}

export interface ImportTombstone {
  code: string;
  deletedAt: string;
}

export interface WatchlistStore {
  readonly kind: string;
  init(): Promise<void>;
  snapshot(): Promise<WatchlistSnapshot>;
  /** 返回 true = 新加入（之前不在清单里）；limit>0 时在同一事务内检查上限 */
  add(code: string, now: Date, limit?: number): Promise<boolean>;
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
  private rev = 0;   // 清单版本（单线程，每个写操作天然串行）

  async init(): Promise<void> {}

  async snapshot(): Promise<WatchlistSnapshot> {
    return { rows: [...this.data.values()].map((r) => ({ ...r })), rev: this.rev };
  }

  /** 一个写操作 = 一个新版本；没有改动则版本不变 */
  private write<T>(fn: (put: (row: Omit<WatchlistRow, 'rev'>) => void) => T): T {
    const next = this.rev + 1;
    let changed = 0;
    const out = fn((row) => { this.data.set(row.code, { ...row, rev: next }); changed++; });
    if (changed > 0) this.rev = next;
    return out;
  }

  async add(code: string, now: Date, limit = 0): Promise<boolean> {
    return this.write((put) => {
      const r = this.data.get(code);
      if (r && r.state === 'active') return false;
      if (limit > 0 && [...this.data.values()].filter((x) => x.state === 'active').length >= limit) {
        throw new WatchlistInputError(`自选股最多 ${limit} 只`);
      }
      put({ code, state: 'active', added_at: iso(now), deleted_at: null, updated_at: iso(now) });
      return true;
    });
  }

  async remove(code: string, now: Date): Promise<void> {
    this.write((put) => {
      const r = this.data.get(code);
      put({ code, state: 'deleted', added_at: r ? r.added_at : iso(now), deleted_at: iso(now), updated_at: iso(now) });
    });
  }

  async clear(now: Date): Promise<number> {
    return this.write((put) => {
      let n = 0;
      for (const r of [...this.data.values()]) {
        if (r.state === 'active') {
          put({ ...r, state: 'deleted', deleted_at: iso(now), updated_at: iso(now) });
          n++;
        }
      }
      return n;
    });
  }

  async importLocal(codes: string[], tombstones: ImportTombstone[], now: Date): Promise<{ added: number; deleted: number }> {
    return this.write((put) => {
      let added = 0, deleted = 0;
      for (const t of tombstones) {
        const r = this.data.get(t.code);
        const at = iso(t.deletedAt);
        if (!r) {
          put({ code: t.code, state: 'deleted', added_at: at, deleted_at: at, updated_at: at });
          deleted++;
        } else if (r.updated_at < at) {
          put({ ...r, state: 'deleted', deleted_at: at, updated_at: at });
          deleted++;
        }
      }
      codes.forEach((code, i) => {
        if (!this.data.has(code)) {
          const at = new Date(now.getTime() + i);   // 每只相差 1ms，保持本机原有顺序
          put({ code, state: 'active', added_at: iso(at), deleted_at: null, updated_at: iso(at) });
          added++;
        }
      });
      return { added, deleted };
    });
  }
}

// ------------------------------------------------------------------ PostgreSQL 实现（生产）

export const PG_TABLE = 'kline_watchlist';
const PG_CLIENT_OPTIONS = { max: 3, idle_timeout: 30, connect_timeout: 10, onnotice: () => {} };

type Sql = any; // postgres-js 实例（运行时加载，避免在无驱动的环境编译失败）

export interface PostgresWatchlistOptions {
  /** 仅测试用：每个写事务提交前调用（此时已持有版本行锁），用于构造并发提交顺序 */
  beforeCommit?: (op: string) => Promise<void>;
}

/**
 * 建表脚本（FIR-42）：由平台 migration（有 CREATE 权限的账号）执行，应用运行时**不建表**。
 * 妙搭的应用账号对 schema 只有 USAGE，没有 CREATE；应用账号只需要两张表的 SELECT / INSERT / UPDATE。
 * 仓库里的 watchlist.migration.sql 与本函数输出一致（测试校验），平台执行那个文件即可；可重复执行。
 */
export function watchlistMigrationSql(table = PG_TABLE): string {
  if (!/^[a-z_][a-z0-9_]*$/.test(table)) throw new Error(`非法表名：${table}`);
  return `-- FIR-42：自选股（全站共用一份）。由平台 migration 执行；应用运行时只做 DML，不建表。
-- 应用账号需要：两张表的 SELECT, INSERT, UPDATE（不需要 DELETE / CREATE）。可重复执行。
CREATE TABLE IF NOT EXISTS ${table} (
  code        text PRIMARY KEY CHECK (code ~ '^[0-9]{6}$'),
  state       text NOT NULL CHECK (state IN ('active', 'deleted')),
  added_at    timestamptz NOT NULL,
  deleted_at  timestamptz,
  updated_at  timestamptz NOT NULL,
  rev         bigint NOT NULL
);
CREATE TABLE IF NOT EXISTS ${table}_meta (
  id   int PRIMARY KEY CHECK (id = 1),
  rev  bigint NOT NULL
);
INSERT INTO ${table}_meta (id, rev) VALUES (1, 0) ON CONFLICT (id) DO NOTHING;
`;
}

/** 测试 / 本地开发用：用有建表权限的连接执行建表脚本。生产不调用（由平台 migration 执行同一份 SQL）。 */
export async function applyWatchlistMigration(sql: Sql, table = PG_TABLE): Promise<void> {
  await sql.unsafe(watchlistMigrationSql(table));
}

/** 把常见的权限 / 缺表错误翻译成可操作的说明（不含连接串） */
function describePgError(err: unknown, table: string): string {
  const code = (err as any)?.code;
  const msg = errMsg(err);
  if (code === '42P01') return `自选股数据表不存在（${table} / ${table}_meta）：应用运行时不建表，需先由平台执行 watchlist.migration.sql（${msg}）`;
  if (code === '42501') return `应用账号缺少自选股表的读写权限（需要 SELECT / INSERT / UPDATE）（${msg}）`;
  return msg;
}

class NoChange extends Error {
  constructor(readonly out: unknown) { super('no change'); }
}

/**
 * 版本控制（QA R73-2；FIR-42 适配平台连接池）：
 * - 版本表 `<table>_meta` 只有一行（id=1, rev）。每个写操作（add/remove/clear/import）的事务第一句是
 *   `UPDATE <meta> SET rev = rev + 1 WHERE id = 1 RETURNING rev`：这一句拿到版本行的行锁并分配新版本，
 *   锁一直持有到提交。跨连接、跨实例的写入按“拿锁顺序 = 提交顺序”串行，版本号与数据同一事务提交。
 *   只用普通 DML 和事务，不用 advisory lock（平台的 transaction pooler 不支持），也不在运行时建表。
 * - 没有实际改动的写操作（重复 add、clear 空清单、迁入无新内容）回滚整个事务，rev 不变。
 * - 读取用一条 SQL 同时取版本行和全部数据行（同一快照），所以返回的 rev 与 items 一定对应同一个已提交状态。
 */
export class PostgresWatchlistStore implements WatchlistStore {
  readonly kind = 'postgres';
  private sql: Sql;
  private readonly metaTable: string;

  constructor(urlOrSql: string | Sql, private readonly table = PG_TABLE, private readonly opts: PostgresWatchlistOptions = {}) {
    if (!/^[a-z_][a-z0-9_]*$/.test(table)) throw new Error(`非法表名：${table}`);
    this.metaTable = `${table}_meta`;
    if (typeof urlOrSql === 'string') {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const postgres = require('postgres');
      this.sql = postgres(urlOrSql, PG_CLIENT_OPTIONS);
    } else {
      this.sql = urlOrSql;
    }
  }

  private t(q: Sql = this.sql) {
    return q(this.table);
  }

  private m(q: Sql = this.sql) {
    return q(this.metaTable);
  }

  /** 只检查、不建表：表存在、可读；版本行缺失时补一行（普通 INSERT） */
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

  async snapshot(): Promise<WatchlistSnapshot> {
    // 单条 SQL：版本行与数据行来自同一快照
    const rs = await this.sql`
      SELECT m.rev AS meta_rev, w.code, w.state, w.added_at, w.deleted_at, w.updated_at, w.rev
      FROM ${this.m()} m LEFT JOIN ${this.t()} w ON true
      WHERE m.id = 1`;
    if (!rs.length) throw new Error('自选股版本记录缺失（未初始化）');
    const rows: WatchlistRow[] = rs.filter((r: any) => r.code !== null).map((r: any) => ({
      code: r.code, state: r.state,
      added_at: iso(r.added_at), deleted_at: r.deleted_at ? iso(r.deleted_at) : null, updated_at: iso(r.updated_at),
      rev: Number(r.rev),
    }));
    return { rows, rev: Number(rs[0].meta_rev) };
  }

  /** 写事务：UPDATE 版本行拿锁并分配 rev → 写数据 → 有改动提交，无改动回滚 */
  private async write<T>(op: string, fn: (tx: Sql, rev: number) => Promise<{ out: T; changed: number }>): Promise<T> {
    try {
      return await this.sql.begin(async (tx: Sql) => {
        const [meta] = await tx`UPDATE ${this.m(tx)} SET rev = rev + 1 WHERE id = 1 RETURNING rev`;
        if (!meta) throw new Error('自选股版本记录缺失（未初始化）');
        const rev = Number(meta.rev);
        const { out, changed } = await fn(tx, rev);
        if (this.opts.beforeCommit) await this.opts.beforeCommit(op);
        if (changed === 0) throw new NoChange(out);   // 回滚：版本不推进
        return out;
      });
    } catch (err) {
      if (err instanceof NoChange) return err.out as T;
      throw err;
    }
  }

  async add(code: string, now: Date, limit = 0): Promise<boolean> {
    return this.write('add', async (tx, rev) => {
      const [cur] = await tx`SELECT state FROM ${this.t(tx)} WHERE code = ${code}`;
      if (cur && cur.state === 'active') return { out: false, changed: 0 };
      if (limit > 0) {
        const [{ n }] = await tx`SELECT count(*)::int AS n FROM ${this.t(tx)} WHERE state = 'active'`;
        if (n >= limit) throw new WatchlistInputError(`自选股最多 ${limit} 只`);
      }
      await tx`
        INSERT INTO ${this.t(tx)} (code, state, added_at, deleted_at, updated_at, rev)
        VALUES (${code}, 'active', ${now}, NULL, ${now}, ${rev})
        ON CONFLICT (code) DO UPDATE
          SET state = 'active', added_at = EXCLUDED.added_at, deleted_at = NULL,
              updated_at = EXCLUDED.updated_at, rev = EXCLUDED.rev`;
      return { out: true, changed: 1 };
    });
  }

  async remove(code: string, now: Date): Promise<void> {
    await this.write('remove', async (tx, rev) => {
      await tx`
        INSERT INTO ${this.t(tx)} (code, state, added_at, deleted_at, updated_at, rev)
        VALUES (${code}, 'deleted', ${now}, ${now}, ${now}, ${rev})
        ON CONFLICT (code) DO UPDATE
          SET state = 'deleted', deleted_at = EXCLUDED.deleted_at, updated_at = EXCLUDED.updated_at, rev = EXCLUDED.rev`;
      return { out: undefined, changed: 1 };
    });
  }

  async clear(now: Date): Promise<number> {
    return this.write('clear', async (tx, rev) => {
      const rs = await tx`
        UPDATE ${this.t(tx)} SET state = 'deleted', deleted_at = ${now}, updated_at = ${now}, rev = ${rev}
        WHERE state = 'active' RETURNING code`;
      return { out: rs.length, changed: rs.length };
    });
  }

  async importLocal(codes: string[], tombstones: ImportTombstone[], now: Date): Promise<{ added: number; deleted: number }> {
    return this.write('import', async (tx, rev) => {
      let added = 0, deleted = 0;
      for (const t of tombstones) {
        const at = new Date(t.deletedAt);
        const rs = await tx`
          INSERT INTO ${this.t(tx)} (code, state, added_at, deleted_at, updated_at, rev)
          VALUES (${t.code}, 'deleted', ${at}, ${at}, ${at}, ${rev})
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
          VALUES (${code}, 'active', ${at}, NULL, ${at}, ${rev})
          ON CONFLICT (code) DO NOTHING
          RETURNING code`;
        added += rs.length;
      }
      return { out: { added, deleted }, changed: added + deleted };
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
  async snapshot(): Promise<WatchlistSnapshot> { return this.fail(); }
  async add(): Promise<boolean> { return this.fail(); }
  async remove(): Promise<void> { return this.fail(); }
  async clear(): Promise<number> { return this.fail(); }
  async importLocal(): Promise<{ added: number; deleted: number }> { return this.fail(); }
}

/**
 * 按环境变量选择实现：WATCHLIST_STORE=memory（仅本地开发）；否则用 WATCHLIST_DATABASE_URL / SUDA_DATABASE_URL。
 *
 * 部署前检查（FIR-41）：postgres 驱动目前是平台包的间接依赖（package.json 未直接声明）。
 * 驱动缺失或连接串格式错误时不能让整个服务启动失败（本函数在 Service 构造时调用），
 * 只让自选股 fail-closed：ready=false、接口 503、/api/health 报原因；个股分析等其他功能照常。
 * 原因里会去掉连接串，避免账号密码出现在 /api/health。
 */
export function createWatchlistStore(
  env: Record<string, string | undefined>,
  loadDriver: () => any = () => require('postgres'),
): WatchlistStore {
  if (env.WATCHLIST_STORE === 'memory') return new MemoryWatchlistStore();
  const url = env.WATCHLIST_DATABASE_URL || env.SUDA_DATABASE_URL;
  if (!url) {
    return new UnavailableWatchlistStore('自选股存储未配置（缺少 SUDA_DATABASE_URL），不在浏览器本地兜底保存');
  }
  try {
    const postgres = loadDriver();
    const sql = postgres(url, PG_CLIENT_OPTIONS);
    return new PostgresWatchlistStore(sql);
  } catch (err) {
    const reason = errMsg(err).split(url).join('***');
    return new UnavailableWatchlistStore(`自选股存储不可用：PostgreSQL 驱动加载或连接串解析失败（${reason}）`);
  }
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
    const { rows, rev } = await this.store.snapshot();
    const active = rows.filter((r) => r.state === 'active')
      .sort((a, b) => (a.added_at < b.added_at ? -1 : a.added_at > b.added_at ? 1 : a.code < b.code ? -1 : 1));
    const tomb = rows.filter((r) => r.state === 'deleted')
      .sort((a, b) => (a.code < b.code ? -1 : 1))
      .map((r) => ({ code: r.code, deleted_at: r.deleted_at as string }));
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
      const added = await this.store.add(c, this.now(), MAX_WATCHLIST);
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
