import { BadRequestException, Injectable, Logger, OnModuleInit } from '@nestjs/common';
import { execSync } from 'child_process';
import * as path from 'path';
import * as fs from 'fs';
import * as dotenv from 'dotenv';
import { assertAsOf, buildBatchItem } from './stock-analysis.pure';
import { Watchlist, WatchlistView, createWatchlistStore } from './watchlist-store';
import { ScanJobManager, ScanFilter } from './scan-jobs';
import { runProcess } from './run-process';

dotenv.config();

interface PythonBinary {
  path: string;
  version: string;
}

interface CallOptions {
  asOf?: string;
  adjustment?: 'qfq' | 'none';
  /** 中止 → 立即 SIGKILL Python 子进程，并在进程确认退出后才 reject（扫描超时用，QA R71-2） */
  signal?: AbortSignal;
}

/** 单次 Python 分析的硬超时：到时 SIGKILL，确认退出后返回（原 spawn timeout 只发 SIGTERM，不保证退出） */
const PY_TIMEOUT_MS = 30000;

const CODE_RE = /^\d{6}$/;
const MAX_SCAN_CODES = 200;

@Injectable()
export class StockAnalysisService implements OnModuleInit {
  private readonly logger = new Logger(StockAnalysisService.name);
  private pythonBinary: PythonBinary | null = null;
  private coreInstalled = false;
  private coreAnalyzer = 'unavailable';
  private coreSource = 'none';
  private coreSha256: string | null = null;
  private analyzerSha256: string | null = null;
  private coreInstallError: string | null = null;
  /** FIR-41：自选股存 PostgreSQL（全站共用一份），不再保存在 BFF 内存或浏览器本地 */
  private readonly watchlist = new Watchlist(createWatchlistStore(process.env));
  /** FIR-41：扫描后台任务（全局最多 3 个 Python 分析同时运行，单只超时，单次 ≤200 只） */
  private readonly scanJobs = new ScanJobManager((code, signal) => this.analyzeOne(code, signal), {
    maxParallel: Number(process.env.SCAN_MAX_PARALLEL ?? 3) || 3,
  });

  onModuleInit(): void {
    this.watchlist.init().then(() => {
      const st = this.watchlist.status();
      if (st.ready) this.logger.log(`自选股存储就绪（${st.kind}）`);
      else this.logger.error(`自选股存储不可用（${st.kind}）：${st.error}`);
    });
    this.pythonBinary = this.detectPythonBinary();
    if (this.pythonBinary) {
      this.setupCoreModule().catch((err: Error) => {
        this.logger.error('core 模块初始化失败', err.message);
      });
    }
  }

  private detectPythonBinary(): PythonBinary | null {
    const candidates = ['python3', 'python'];
    for (const bin of candidates) {
      try {
        const output = execSync(`${bin} --version 2>&1`, { encoding: 'utf8', timeout: 3000 }).trim();
        if (output.includes('Python')) {
          return {
            path: bin,
            version: output.match(/Python\s+([\d.]+)/)?.[1] || 'unknown',
          };
        }
      } catch {
        // continue
      }
    }
    return null;
  }

  private checkCoreInstalled(): boolean {
    const coreDir = path.join(process.cwd(), 'core');
    const initFile = path.join(coreDir, '__init__.py');
    return fs.existsSync(coreDir) && fs.existsSync(initFile);
  }

  private async setupCoreModule(): Promise<void> {
    if (!this.pythonBinary) return;

    if (this.checkCoreInstalled()) {
      try {
        const scriptPath = path.join(process.cwd(), 'python_analyzer.py');
        const output = execSync(
          `${this.pythonBinary.path} ${scriptPath} --mode health`,
          { encoding: 'utf8', timeout: 5000, cwd: process.cwd() }
        );
        const data = JSON.parse(output.trim()) as {
          core_installed?: boolean; analyzer?: string; core_version?: string;
          core_sha256?: string | null; analyzer_sha256?: string | null;
        };
        this.coreInstalled = data.core_installed ?? true;
        this.coreAnalyzer = data.analyzer || 'unknown';
        this.coreSource = data.core_version || 'github_api';
        this.coreSha256 = data.core_sha256 ?? null;
        this.analyzerSha256 = data.analyzer_sha256 ?? null;
      } catch (err) {
        this.coreInstalled = true;
        this.coreAnalyzer = 'error';
        this.coreInstallError = err instanceof Error ? err.message : String(err);
      }
      return;
    }

    this.coreInstalled = false;
    this.coreAnalyzer = 'not_configured';
  }

  getPythonInfo(): { available: boolean; version: string | null; path: string | null } {
    if (!this.pythonBinary) {
      return { available: false, version: null, path: null };
    }
    return {
      available: true,
      version: this.pythonBinary.version,
      path: this.pythonBinary.path,
    };
  }

  getCoreStatus(): {
    installed: boolean; analyzer: string; source: string; installError: string | null;
    coreSha256: string | null; analyzerSha256: string | null; engineCommit: string | null;
  } {
    return {
      installed: this.coreInstalled,
      analyzer: this.coreAnalyzer,
      source: this.coreSource,
      installError: this.coreInstallError,
      // 线上版本自证：与 `python3 scripts/v6_fingerprint.py --ref <tag>` 的输出逐项比对
      coreSha256: this.coreSha256,
      analyzerSha256: this.analyzerSha256,
      engineCommit: process.env.ENGINE_COMMIT ?? null,
    };
  }

  getRunMode(): string {
    if (this.pythonBinary) return 'local';
    return 'unavailable';
  }

  private validateCode(code: string): void {
    if (!CODE_RE.test(code)) {
      throw new BadRequestException(`股票代码必须为 6 位数字：${JSON.stringify(code)}`);
    }
  }

  /** 调用 Python Canonical Core 执行器。严格 6 位代码、spawn 参数数组（无 shell 注入）、fail-closed。 */
  callPythonLocal(code: string, opts: CallOptions = {}): Promise<Record<string, unknown>> {
    this.validateCode(code);
    if (!this.pythonBinary) {
      return Promise.reject(new Error('本地Python环境不可用'));
    }
    const scriptPath = path.join(process.cwd(), 'python_analyzer.py');
    const args = [scriptPath, code, '--adjustment', opts.adjustment ?? 'qfq'];
    let asOf: string | undefined;
    try {
      asOf = assertAsOf(opts.asOf);
    } catch (err) {
      return Promise.reject(err);
    }
    if (asOf) args.push('--as-of', asOf);
    // 生产路径不传任何降级开关：Python 侧 fail-closed，拿不到 qfq / core 失败直接非零退出。
    return runProcess(this.pythonBinary.path, args, {
      cwd: process.cwd(),
      timeoutMs: PY_TIMEOUT_MS,
      signal: opts.signal,
      env: { ...process.env, ENGINE_COMMIT: process.env.ENGINE_COMMIT ?? undefined },
    }).then((r) => {
      if (r.killed === 'timeout') throw new Error(`Python 分析超时（>${PY_TIMEOUT_MS / 1000} 秒），已终止进程`);
      if (r.killed === 'aborted') throw new Error('分析已中止，Python 进程已终止');
      if (r.exitCode !== 0) throw new Error(r.stderr.trim() || `Python脚本退出码 ${r.exitCode}`);
      let result: any;
      try {
        result = JSON.parse(r.stdout.trim());
      } catch (err) {
        throw new Error('Python输出解析失败: ' + (err instanceof Error ? err.message : String(err)));
      }
      const analyzer = result?.engine?.analyzer;
      if (analyzer && analyzer !== 'real_core') {
        throw new Error(`引擎非 real_core（${analyzer}），生产路径拒绝降级结果`);
      }
      return result as Record<string, unknown>;
    });
  }

  async analyzeStock(code: string, asOf?: string): Promise<Record<string, unknown>> {
    this.validateCode(code);
    if (!this.pythonBinary) {
      throw new Error('无可用的Python分析后端');
    }
    const data = await this.callPythonLocal(code, { asOf });
    return { ...data, run_mode: 'local' };
  }

  /** 真实大盘环境（腾讯上证指数，不复权），与前端本地 JS 计算并存、仅作参考。 */
  async getMarketEnv(): Promise<Record<string, unknown>> {
    if (!this.pythonBinary) {
      throw new Error('无可用的Python分析后端');
    }
    const doc = await this.callPythonMarket();
    return { ...(doc.market_env as Record<string, unknown>), run_mode: 'local', provenance: doc.provenance };
  }

  private callPythonMarket(asOf?: string): Promise<Record<string, unknown>> {
    if (!this.pythonBinary) {
      return Promise.reject(new Error('本地Python环境不可用'));
    }
    const scriptPath = path.join(process.cwd(), 'python_analyzer.py');
    const args = [scriptPath, '--mode', 'market'];
    let checked: string | undefined;
    try {
      checked = assertAsOf(asOf);
    } catch (err) {
      return Promise.reject(err);
    }
    if (checked) args.push('--as-of', checked);
    return runProcess(this.pythonBinary.path, args, {
      cwd: process.cwd(),
      timeoutMs: PY_TIMEOUT_MS,
      env: { ...process.env, ENGINE_COMMIT: process.env.ENGINE_COMMIT ?? undefined },
    }).then((r) => {
      if (r.killed) throw new Error(`大盘分析超时（>${PY_TIMEOUT_MS / 1000} 秒），已终止进程`);
      if (r.exitCode !== 0) throw new Error(r.stderr.trim() || `大盘分析退出码 ${r.exitCode}`);
      try {
        return JSON.parse(r.stdout.trim());
      } catch (err) {
        throw new Error('大盘输出解析失败: ' + (err instanceof Error ? err.message : String(err)));
      }
    });
  }

  getWatchlistStatus(): { kind: string; ready: boolean; error: string | null } {
    return this.watchlist.status();
  }

  getWatchlist(): Promise<WatchlistView> {
    return this.watchlist.view();
  }

  addToWatchlist(code: string): Promise<WatchlistView & { added: boolean }> {
    return this.watchlist.add(code);
  }

  removeFromWatchlist(code: string): Promise<WatchlistView> {
    return this.watchlist.remove(code);
  }

  clearWatchlist(): Promise<WatchlistView & { cleared: number }> {
    return this.watchlist.clear();
  }

  importWatchlist(body: unknown): Promise<WatchlistView & { added: number; deleted: number; skipped: string[] }> {
    return this.watchlist.importLocal(body);
  }

  /** 扫描列表项：确认 / 主导全部透传 Python 信号层（见 stock-analysis.pure.ts，R67-1）。 */
  private buildBatchItem(doc: Record<string, any>): Record<string, unknown> {
    return buildBatchItem(doc);
  }

  createScanJob(codes: unknown, filter: ScanFilter): { job_id: string; total: number } {
    if (!this.pythonBinary) {
      throw new Error('无可用的Python分析后端');
    }
    return this.scanJobs.create(codes, filter);
  }

  getScanJob(id: string): Record<string, unknown> | null {
    return this.scanJobs.get(id);
  }

  cancelScanJob(id: string): boolean {
    return this.scanJobs.cancel(id);
  }

  private async analyzeOne(code: string, signal?: AbortSignal): Promise<Record<string, unknown>> {
    return this.buildBatchItem((await this.callPythonLocal(code, { signal })) as Record<string, any>);
  }

  async analyzeWatchlist(): Promise<{
    items: Array<Record<string, unknown>>;
    failed: Array<{ code: string; reason: string }>;
    count: number;
    failedCount: number;
    run_mode: string;
  }> {
    const codes = await this.watchlist.codes();
    if (codes.length === 0) {
      return { items: [], failed: [], count: 0, failedCount: 0, run_mode: 'local' };
    }
    if (!this.pythonBinary) {
      throw new Error('无可用的Python分析后端');
    }
    const { items, failed } = await this.runCodes(codes, 0, false);
    return { items, failed, count: items.length, failedCount: failed.length, run_mode: 'local' };
  }

  async scanStocks(codes: string[], minScore = 0, onlyBull = false): Promise<{
    items: Array<Record<string, unknown>>;
    failed: Array<{ code: string; reason: string }>;
    count: number;
    failedCount: number;
    run_mode: string;
  }> {
    if (!this.pythonBinary) {
      throw new Error('无可用的Python分析后端');
    }
    const clean = [...new Set((codes ?? []).map((c) => String(c).trim()))];
    if (clean.some((c) => !CODE_RE.test(c))) {
      throw new BadRequestException('扫描列表包含非法代码（必须为 6 位数字）');
    }
    if (clean.length > MAX_SCAN_CODES) {
      throw new BadRequestException(`单次扫描最多 ${MAX_SCAN_CODES} 只（当前 ${clean.length}）`);
    }
    const { items, failed } = await this.runCodes(clean, minScore, onlyBull);
    return { items, failed, count: items.length, failedCount: failed.length, run_mode: 'local' };
  }

  /** 串行逐只跑 Python core（有限并发在后续阶段实现）。返回经过滤/排序的列表与失败明细。 */
  private async runCodes(
    codes: string[], minScore: number, onlyBull: boolean
  ): Promise<{
    items: Array<Record<string, unknown>>;
    failed: Array<{ code: string; reason: string }>;
  }> {
    const all: Array<Record<string, unknown>> = [];
    const failed: Array<{ code: string; reason: string }> = [];
    for (const code of codes) {
      try {
        const item = await this.analyzeOne(code);
        const bull = (item.bull_top as any) ?? null;
        if (onlyBull && bull === null) continue;
        if (minScore > 0 && (bull === null || bull.score < minScore)) continue;
        all.push(item);
      } catch (err) {
        failed.push({ code, reason: err instanceof Error ? err.message : String(err) });
      }
    }
    const scoreOf = (it: Record<string, unknown>) => ((it.bull_top as any)?.score ?? -1);
    all.sort((a, b) => scoreOf(b) - scoreOf(a));
    return { items: all, failed };
  }

  getSectors(): { items: Array<Record<string, unknown>>; run_mode: string; note: string } {
    // 不返回写死的假板块成分；板块成分扫描在阶段 2 以后台任务方式提供，BFF 不伪造。
    return { items: [], run_mode: 'local', note: '板块成分扫描暂未开放（阶段 2 后台任务），BFF 不提供写死成分' };
  }

  async scanSector(code: string): Promise<never> {
    throw new BadRequestException('板块扫描暂未开放（阶段 2 后台任务），BFF 不提供写死成分股');
  }

  getSyncConfig(): Record<string, unknown> {
    return {
      mode: 'server',
      storage: this.watchlist.status(),
      detail: '自选股保存在服务端数据库（全站共用一份），手机 / 电脑打开即同步；不再使用 GitHub 令牌',
      run_mode: 'local',
    };
  }

  syncPull(): { error: string; detail: string; run_mode: string } {
    return {
      error: '已停用：自选股直接读写服务端（GET /api/watchlist），无需手动同步',
      detail: '浏览器与服务端都不再保存 GitHub 令牌',
      run_mode: 'local',
    };
  }
}
