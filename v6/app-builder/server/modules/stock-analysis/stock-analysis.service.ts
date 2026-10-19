import { BadRequestException, Injectable, Logger, OnModuleInit } from '@nestjs/common';
import { spawn, execSync } from 'child_process';
import * as path from 'path';
import * as fs from 'fs';
import * as dotenv from 'dotenv';
import { assertAsOf, buildBatchItem } from './stock-analysis.pure';

dotenv.config();

interface PythonBinary {
  path: string;
  version: string;
}

interface CallOptions {
  asOf?: string;
  adjustment?: 'qfq' | 'none';
}

const CODE_RE = /^\d{6}$/;
const MAX_SCAN_CODES = 200;

@Injectable()
export class StockAnalysisService implements OnModuleInit {
  private readonly logger = new Logger(StockAnalysisService.name);
  private pythonBinary: PythonBinary | null = null;
  private coreInstalled = false;
  private coreAnalyzer = 'unavailable';
  private coreSource = 'none';
  private coreInstallError: string | null = null;
  private watchlist: string[] = [];

  onModuleInit(): void {
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
        };
        this.coreInstalled = data.core_installed ?? true;
        this.coreAnalyzer = data.analyzer || 'unknown';
        this.coreSource = data.core_version || 'github_api';
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

  getCoreStatus(): { installed: boolean; analyzer: string; source: string; installError: string | null } {
    return {
      installed: this.coreInstalled,
      analyzer: this.coreAnalyzer,
      source: this.coreSource,
      installError: this.coreInstallError,
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
    return new Promise((resolve, reject) => {
      if (!this.pythonBinary) {
        reject(new Error('本地Python环境不可用'));
        return;
      }

      const scriptPath = path.join(process.cwd(), 'python_analyzer.py');
      const args = [scriptPath, code, '--adjustment', opts.adjustment ?? 'qfq'];
      let asOf: string | undefined;
      try {
        asOf = assertAsOf(opts.asOf);
      } catch (err) {
        reject(err);
        return;
      }
      if (asOf) args.push('--as-of', asOf);
      const py = spawn(this.pythonBinary.path, args, {
        cwd: process.cwd(),
        timeout: 30000,
        env: {
          ...process.env,
          ENGINE_COMMIT: process.env.ENGINE_COMMIT ?? undefined,
        },
      });
      // 生产路径不传任何降级开关：Python 侧 fail-closed，拿不到 qfq / core 失败直接非零退出。

      let stdout = '';
      let stderr = '';

      py.stdout.on('data', (chunk: Buffer) => {
        stdout += chunk.toString();
      });

      py.stderr.on('data', (chunk: Buffer) => {
        stderr += chunk.toString();
      });

      py.on('close', (codeExit: number) => {
        if (codeExit !== 0) {
          reject(new Error(stderr.trim() || `Python脚本退出码 ${codeExit}`));
          return;
        }
        try {
          const result = JSON.parse(stdout.trim());
          const analyzer = result?.engine?.analyzer;
          if (analyzer && analyzer !== 'real_core') {
            reject(new Error(`引擎非 real_core（${analyzer}），生产路径拒绝降级结果`));
            return;
          }
          resolve(result);
        } catch (err) {
          reject(new Error('Python输出解析失败: ' + (err instanceof Error ? err.message : String(err))));
        }
      });

      py.on('error', (err: Error) => {
        reject(err);
      });
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
    return new Promise((resolve, reject) => {
      if (!this.pythonBinary) {
        reject(new Error('本地Python环境不可用'));
        return;
      }
      const scriptPath = path.join(process.cwd(), 'python_analyzer.py');
      const args = [scriptPath, '--mode', 'market'];
      let checked: string | undefined;
      try {
        checked = assertAsOf(asOf);
      } catch (err) {
        reject(err);
        return;
      }
      if (checked) args.push('--as-of', checked);
      const py = spawn(this.pythonBinary.path, args, {
        cwd: process.cwd(),
        timeout: 30000,
        env: {
          ...process.env,
          ENGINE_COMMIT: process.env.ENGINE_COMMIT ?? undefined,
        },
      });
      let stdout = '';
      let stderr = '';
      py.stdout.on('data', (c: Buffer) => { stdout += c.toString(); });
      py.stderr.on('data', (c: Buffer) => { stderr += c.toString(); });
      py.on('close', (exit: number) => {
        if (exit !== 0) {
          reject(new Error(stderr.trim() || `大盘分析退出码 ${exit}`));
          return;
        }
        try {
          resolve(JSON.parse(stdout.trim()));
        } catch (err) {
          reject(new Error('大盘输出解析失败: ' + (err instanceof Error ? err.message : String(err))));
        }
      });
      py.on('error', reject);
    });
  }

  getWatchlist(): { items: string[]; count: number } {
    return { items: [...this.watchlist], count: this.watchlist.length };
  }

  addToWatchlist(code: string): { success: boolean; code: string; watchlist: string[] } {
    this.validateCode(code);
    if (this.watchlist.includes(code)) {
      throw Object.assign(new Error('已在自选股列表中'), { statusCode: 409, code });
    }
    this.watchlist.push(code);
    return { success: true, code, watchlist: [...this.watchlist] };
  }

  removeFromWatchlist(code: string): { success: boolean; code: string; watchlist: string[] } {
    const index = this.watchlist.indexOf(code);
    if (index === -1) {
      throw Object.assign(new Error('不在自选股列表中'), { statusCode: 404, code });
    }
    this.watchlist.splice(index, 1);
    return { success: true, code, watchlist: [...this.watchlist] };
  }

  /** 扫描列表项：确认 / 主导全部透传 Python 信号层（见 stock-analysis.pure.ts，R67-1）。 */
  private buildBatchItem(doc: Record<string, any>): Record<string, unknown> {
    return buildBatchItem(doc);
  }

  private async analyzeOne(code: string): Promise<Record<string, unknown>> {
    return this.buildBatchItem((await this.callPythonLocal(code)) as Record<string, any>);
  }

  async analyzeWatchlist(): Promise<{
    items: Array<Record<string, unknown>>;
    failed: Array<{ code: string; reason: string }>;
    count: number;
    failedCount: number;
    run_mode: string;
  }> {
    if (this.watchlist.length === 0) {
      return { items: [], failed: [], count: 0, failedCount: 0, run_mode: 'local' };
    }
    if (!this.pythonBinary) {
      throw new Error('无可用的Python分析后端');
    }
    const { items, failed } = await this.runCodes(this.watchlist, 0, false);
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
      mode: 'browser_github',
      detail: '自选股在浏览器本地维护并通过 GitHub Contents API 多端同步，BFF 不保存 token',
      run_mode: 'local',
    };
  }

  syncPull(): { error: string; detail: string; run_mode: string } {
    return {
      error: '自选股同步由浏览器端 GitHub Contents API 完成',
      detail: 'BFF 不持有 GitHub token，避免第三方脚本读取凭据',
      run_mode: 'local',
    };
  }
}
