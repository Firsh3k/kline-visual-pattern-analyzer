/**
 * FIR-41 第 1 项：扫描后台任务（无 Nest 依赖，CI 用 tests/v6/test_scan_jobs.js 直接测试）。
 *
 * - 提交任务立即返回任务 ID，前端轮询进度，不再一个 HTTP 请求等到底（200 只串行会超时）。
 * - 全局限制同时运行的分析数（所有任务共用，默认 3 个 Python 进程），单只股票超时，股票代码去重，单次上限。
 * - 每只股票的结果仍由 runner（= Python core + buildBatchItem）给出，与个股页同源；本模块不做任何判断。
 * - 任务状态放内存：服务重启即丢失，查询返回 404，前端提示重新扫描（持久化随自选股后端存储一起做）。
 */
import { randomUUID } from 'crypto';
import { InvalidRequestError } from './stock-analysis.pure';

export type ScanRunner = (code: string) => Promise<Record<string, any>>;

export interface ScanJobOptions {
  maxParallel?: number;      // 全局同时运行的分析数
  maxCodes?: number;         // 单个任务最多股票数
  maxActiveJobs?: number;    // 同时未结束的任务数
  perStockTimeoutMs?: number;
  retentionMs?: number;      // 已结束任务保留多久
  now?: () => number;
}

export interface ScanFilter {
  minScore?: number;
  onlyBull?: boolean;
}

type JobStatus = 'running' | 'done' | 'cancelled';

interface Job {
  id: string;
  codes: string[];
  filter: Required<ScanFilter>;
  next: number;                       // 下一个待启动的股票下标
  running: number;
  done: number;
  items: Array<Record<string, any>>;  // 已成功且通过筛选的结果
  filteredOut: number;
  failed: Array<{ code: string; reason: string }>;
  status: JobStatus;
  createdAt: number;
  finishedAt: number | null;
}

const CODE_RE = /^\d{6}$/;

export class ScanJobManager {
  private readonly jobs = new Map<string, Job>();
  private readonly order: string[] = [];   // 创建顺序，用于公平轮转
  private rr = 0;
  private active = 0;
  private readonly opt: Required<ScanJobOptions>;

  constructor(private readonly runner: ScanRunner, opts: ScanJobOptions = {}) {
    this.opt = {
      maxParallel: opts.maxParallel ?? 3,
      maxCodes: opts.maxCodes ?? 200,
      maxActiveJobs: opts.maxActiveJobs ?? 3,
      perStockTimeoutMs: opts.perStockTimeoutMs ?? 35000,
      retentionMs: opts.retentionMs ?? 30 * 60 * 1000,
      now: opts.now ?? (() => Date.now()),
    };
  }

  /** 提交任务：校验 → 去重 → 入队，立即返回 */
  create(codes: unknown, filter: ScanFilter = {}): { job_id: string; total: number } {
    this.purge();
    if (!Array.isArray(codes) || codes.length === 0) throw new InvalidRequestError('股票代码列表不能为空');
    const clean = [...new Set(codes.map((c) => String(c).trim()))];
    const bad = clean.filter((c) => !CODE_RE.test(c));
    if (bad.length) throw new InvalidRequestError(`扫描列表包含非法代码（必须为 6 位数字）：${bad.slice(0, 5).join(', ')}`);
    if (clean.length > this.opt.maxCodes) {
      throw new InvalidRequestError(`单次扫描最多 ${this.opt.maxCodes} 只（当前 ${clean.length}）`);
    }
    const activeJobs = [...this.jobs.values()].filter((j) => j.status === 'running').length;
    if (activeJobs >= this.opt.maxActiveJobs) {
      throw new InvalidRequestError(`当前已有 ${activeJobs} 个扫描在运行，请等待完成后再提交`);
    }
    const minScore = Number(filter.minScore ?? 0);
    const job: Job = {
      id: randomUUID(),
      codes: clean,
      filter: { minScore: Number.isFinite(minScore) ? minScore : 0, onlyBull: !!filter.onlyBull },
      next: 0, running: 0, done: 0, items: [], filteredOut: 0, failed: [],
      status: 'running', createdAt: this.opt.now(), finishedAt: null,
    };
    this.jobs.set(job.id, job);
    this.order.push(job.id);
    this.pump();
    return { job_id: job.id, total: clean.length };
  }

  /** 查询进度与结果；任务不存在（含服务重启后）返回 null */
  get(id: string): Record<string, unknown> | null {
    this.purge();
    const j = this.jobs.get(id);
    if (!j) return null;
    const scoreOf = (it: Record<string, any>) => (it.bull_top?.score ?? -1);
    return {
      job_id: j.id,
      status: j.status,
      total: j.codes.length,
      done: j.done,
      running: j.running,
      pending: j.codes.length - j.next,
      succeeded: j.items.length + j.filteredOut,
      matched: j.items.length,
      filtered_out: j.filteredOut,
      failed_count: j.failed.length,
      items: [...j.items].sort((a, b) => scoreOf(b) - scoreOf(a)),
      failed: [...j.failed],
      filter: { min_score: j.filter.minScore, only_bull: j.filter.onlyBull },
      created_at: new Date(j.createdAt).toISOString(),
      finished_at: j.finishedAt === null ? null : new Date(j.finishedAt).toISOString(),
      storage: 'memory',
    };
  }

  /** 取消：未启动的股票不再分析；正在跑的跑完后结果照常记录 */
  cancel(id: string): boolean {
    const j = this.jobs.get(id);
    if (!j) return false;
    if (j.status === 'running') {
      j.status = 'cancelled';
      j.next = j.codes.length;
      if (j.running === 0) j.finishedAt = this.opt.now();
    }
    return true;
  }

  /** 测试 / 监控用：当前全局在跑的分析数 */
  activeCount(): number {
    return this.active;
  }

  private pickJob(): Job | null {
    const ids = this.order.filter((id) => {
      const j = this.jobs.get(id);
      return j && j.status === 'running' && j.next < j.codes.length;
    });
    if (!ids.length) return null;
    const j = this.jobs.get(ids[this.rr % ids.length])!;
    this.rr += 1;
    return j;
  }

  private pump(): void {
    while (this.active < this.opt.maxParallel) {
      const job = this.pickJob();
      if (!job) return;
      const code = job.codes[job.next++];
      job.running += 1;
      this.active += 1;
      this.runOne(job, code).finally(() => {
        job.running -= 1;
        job.done += 1;
        this.active -= 1;
        if (job.running === 0 && job.next >= job.codes.length && job.finishedAt === null) {
          if (job.status === 'running') job.status = 'done';
          job.finishedAt = this.opt.now();
        }
        this.pump();
      });
    }
  }

  private async runOne(job: Job, code: string): Promise<void> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      const timeout = new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error(`分析超时（>${Math.round(this.opt.perStockTimeoutMs / 1000)} 秒）`)),
          this.opt.perStockTimeoutMs);
      });
      const item = await Promise.race([this.runner(code), timeout]);
      const bull = item?.bull_top ?? null;
      const { minScore, onlyBull } = job.filter;
      if ((onlyBull && bull === null) || (minScore > 0 && (bull === null || bull.score < minScore))) {
        job.filteredOut += 1;
      } else {
        job.items.push(item);
      }
    } catch (err) {
      job.failed.push({ code, reason: err instanceof Error ? err.message : String(err) });
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  private purge(): void {
    const now = this.opt.now();
    for (const [id, j] of this.jobs) {
      if (j.finishedAt !== null && now - j.finishedAt > this.opt.retentionMs) {
        this.jobs.delete(id);
        const k = this.order.indexOf(id);
        if (k >= 0) this.order.splice(k, 1);
      }
    }
  }
}
