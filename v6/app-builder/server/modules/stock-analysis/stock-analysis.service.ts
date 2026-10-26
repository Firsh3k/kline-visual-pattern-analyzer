import { BadRequestException, Injectable, Logger, OnModuleInit } from '@nestjs/common';
import { execSync } from 'child_process';
import * as path from 'path';
import * as fs from 'fs';
import * as os from 'os';
import * as zlib from 'zlib';
import * as dotenv from 'dotenv';
import { assertAsOf, buildBatchItem } from './stock-analysis.pure';
import { Watchlist, WatchlistStatus, WatchlistView, createWatchlistStore } from './watchlist-store';
import { ScanJobManager, ScanFilter } from './scan-jobs';
import { runProcess } from './run-process';
import { ENGINE_BUNDLE_PAYLOAD, ENGINE_BUNDLE_FILE_COUNT } from './engine-bundle';

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

  /**
   * 定位 Python 引擎包：python_analyzer.py 与 core/ 必须同目录（analyzer 用 __file__ 定位并 import core）。
   * 兼容多种部署/打包布局：
   *  - 运行根：/opt/bytefaas（rsync 全量或标准构建把引擎放 dist 根）
   *  - 运行根 server/：/opt/bytefaas/server（云端构建 dist/server 被整体打包进运行时）
   *  - 静态资源兜底目录：/opt/bytefaas/dist/client/_engine
   *  - 以及编译产物 __dirname 相对路径（dist/server/modules/stock-analysis）
   */
  private resolveEngine(): { script: string; workdir: string } | null {
    const cwd = process.cwd();
    const here = __dirname;
    // 1) 真实文件系统候选（本地开发 / rsync 直传，.py 直接可达）
    const bases = Array.from(
      new Set([
        cwd,
        path.join(cwd, 'server'),
        path.join(cwd, 'dist', 'client', '_engine'),
        path.resolve(here, '..', '..'),
        path.resolve(here, '..', '..', '..'),
      ]),
    );
    for (const base of bases) {
      const script = path.join(base, 'python_analyzer.py');
      const initFile = path.join(base, 'core', '__init__.py');
      if (fs.existsSync(script) && fs.existsSync(initFile)) {
        return { script, workdir: base };
      }
    }
    // 2) 生产：妙搭部署排除 .py，从内嵌 engine-bundle.js 解压到临时目录
    return this.materializeEngine();
  }

  /**
   * 从被静态 import 的 engine-bundle.ts（gzip+base64 文件映射常量）解压 Python 引擎到
   * 临时目录。妙搭运行时 /opt/bytefaas 不含 .py（平台过滤），且 server 只部署 nest
   * 模块图跟踪的文件，故引擎必须是被 import 的 .ts 模块（随编译部署）；运行时同步
   * 解压到 os.tmpdir（已确认可写），再 spawn python。用 payload 指纹做幂等：同一实例
   * 只解压一次，bundle 变更（payload 前缀变化）才重写。
   */
  private materializeEngine(): { script: string; workdir: string } | null {
    const payload: string = ENGINE_BUNDLE_PAYLOAD;
    if (!payload) {
      this.coreInstallError = 'engine-bundle 为空';
      return null;
    }

    const workdir = path.join(os.tmpdir(), 'kline_engine');
    const stamp = path.join(workdir, '.materialized');
    const fingerprint = payload.slice(0, 40);
    let needWrite = true;
    try {
      needWrite = !(fs.existsSync(stamp) && fs.readFileSync(stamp, 'utf8') === fingerprint);
    } catch {
      needWrite = true;
    }

    if (needWrite) {
      let files: Record<string, string>;
      try {
        const json = zlib.gunzipSync(Buffer.from(payload, 'base64')).toString('utf8');
        files = JSON.parse(json) as Record<string, string>;
      } catch (err) {
        this.coreInstallError = 'engine-bundle 解压失败: ' + (err instanceof Error ? err.message : String(err));
        return null;
      }
      fs.mkdirSync(workdir, { recursive: true });
      for (const [rel, b64] of Object.entries(files)) {
        const full = path.join(workdir, rel);
        fs.mkdirSync(path.dirname(full), { recursive: true });
        fs.writeFileSync(full, Buffer.from(b64, 'base64'));
      }
      fs.writeFileSync(stamp, fingerprint);
    }

    const script = path.join(workdir, 'python_analyzer.py');
    if (fs.existsSync(script) && fs.existsSync(path.join(workdir, 'core', '__init__.py'))) {
      return { script, workdir };
    }
    return null;
  }

  private checkCoreInstalled(): boolean {
    return this.resolveEngine() !== null;
  }

  private async setupCoreModule(): Promise<void> {
    if (!this.pythonBinary) return;

    const engine = this.resolveEngine();
    if (engine) {
      try {
        const output = execSync(
          `"${this.pythonBinary.path}" "${engine.script}" --mode health`,
          { encoding: 'utf8', timeout: 8000, cwd: engine.workdir }
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
    const engine = this.resolveEngine();
    if (!engine) {
      // fail-closed：引擎文件缺失不允许静默降级，直接报错并在 health 暴露 not_configured
      return Promise.reject(
        new Error('Python 引擎未部署：运行目录找不到 python_analyzer.py 或 core/，请检查构建归集'),
      );
    }
    const args = [engine.script, code, '--adjustment', opts.adjustment ?? 'qfq'];
    let asOf: string | undefined;
    try {
      asOf = assertAsOf(opts.asOf);
    } catch (err) {
      return Promise.reject(err);
    }
    if (asOf) args.push('--as-of', asOf);
    // 生产路径不传任何降级开关：Python 侧 fail-closed，拿不到 qfq / core 失败直接非零退出。
    return runProcess(this.pythonBinary.path, args, {
      cwd: engine.workdir,
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
    const engine = this.resolveEngine();
    if (!engine) {
      return Promise.reject(
        new Error('Python 引擎未部署：运行目录找不到 python_analyzer.py 或 core/，请检查构建归集'),
      );
    }
    const args = [engine.script, '--mode', 'market'];
    let checked: string | undefined;
    try {
      checked = assertAsOf(asOf);
    } catch (err) {
      return Promise.reject(err);
    }
    if (checked) args.push('--as-of', checked);
    return runProcess(this.pythonBinary.path, args, {
      cwd: engine.workdir,
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

  /** 真实读取一次后返回状态（/api/health 用，QA R73-3：反映当前是否真的可用） */
  checkWatchlist(): Promise<WatchlistStatus> {
    return this.watchlist.check();
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
