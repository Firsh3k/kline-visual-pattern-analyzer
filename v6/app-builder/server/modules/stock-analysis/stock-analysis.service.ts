import { Injectable, Logger, OnModuleInit } from '@nestjs/common';
import { spawn, execSync } from 'child_process';
import * as path from 'path';
import * as fs from 'fs';

interface PythonBinary {
  path: string;
  version: string;
}

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
        const data = JSON.parse(output.trim()) as { core_installed?: boolean; analyzer?: string };
        this.coreInstalled = data.core_installed ?? true;
        this.coreAnalyzer = data.analyzer || 'unknown';
        this.coreSource = 'github_api';
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

  callPythonLocal(code: string, days = 250): Promise<Record<string, unknown>> {
    return new Promise((resolve, reject) => {
      if (!this.pythonBinary) {
        reject(new Error('本地Python环境不可用'));
        return;
      }

      const scriptPath = path.join(process.cwd(), 'python_analyzer.py');
      // ⚠️ 不传 --allow-skeleton：生产路径 fail-closed，core 失败时 Python 非零退出，
      // reject 后由 Controller 转成明确错误，前端显示红色错误面板，绝不静默降级到假数据。
      const py = spawn(this.pythonBinary.path, [scriptPath, code, '--days', String(days)], {
        cwd: process.cwd(),
        timeout: 30000,
      });

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
          // 双保险：即使脚本意外返回 fallback_skeleton，也拒绝
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

  normalizePatterns(patternsRaw: unknown): Array<{ name: string; direction: string; score: number; detected: boolean }> {
    if (!patternsRaw || typeof patternsRaw !== 'object') return [];
    const obj = patternsRaw as Record<string, unknown>;
    const result: Array<{ name: string; direction: string; score: number; detected: boolean }> = [];
    for (const key of Object.keys(obj)) {
      const v = obj[key] as Record<string, unknown> | undefined;
      if (v && typeof v === 'object' && v.detected === true) {
        result.push({
          name: String(v.name ?? key),
          direction: String(v.direction ?? 'none'),
          score: typeof v.score === 'number' ? v.score : 0,
          detected: true,
        });
      }
    }
    return result;
  }

  async analyzeStock(code: string): Promise<Record<string, unknown>> {
    if (!this.pythonBinary) {
      throw new Error('无可用的Python分析后端');
    }
    const data = await this.callPythonLocal(code);
    return { ...data, run_mode: 'local' };
  }

  getMarketEnv(): Record<string, unknown> {
    return {
      index: '000001.SH',
      name: '上证指数',
      price: 3150.0,
      change_percent: 0.52,
      score: 58,
      stage: '震荡整理',
      stage_desc: '当前市场处于震荡整理阶段，多空力量相对均衡，建议等待方向明确后再操作。',
      run_mode: 'local',
    };
  }

  getWatchlist(): { items: string[]; count: number } {
    return { items: [...this.watchlist], count: this.watchlist.length };
  }

  addToWatchlist(code: string): { success: boolean; code: string; watchlist: string[] } {
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

  async analyzeWatchlist(): Promise<{ items: Array<Record<string, unknown>>; count: number; run_mode: string }> {
    if (this.watchlist.length === 0) {
      return { items: [], count: 0, run_mode: 'local' };
    }
    if (!this.pythonBinary) {
      throw new Error('无可用的Python分析后端');
    }
    const results: Array<Record<string, unknown>> = [];
    for (const code of this.watchlist) {
      const item = await this.callPythonLocal(code) as {
        code: string; name: string; score: number;
        patterns?: Array<{ name: string; direction: string }>;
      };
      const normalized = this.normalizePatterns(item.patterns);
      results.push({
        code: item.code,
        name: item.name,
        score: item.score,
        patterns: normalized.map((p) => ({ name: p.name, direction: p.direction })),
      });
    }
    results.sort((a, b) => (b.score as number) - (a.score as number));
    return { items: results, count: results.length, run_mode: 'local' };
  }

  async scanStocks(codes: string[], minScore = 0, onlyBull = false): Promise<{
    items: Array<Record<string, unknown>>;
    count: number;
    run_mode: string;
  }> {
    if (!this.pythonBinary) {
      throw new Error('无可用的Python分析后端');
    }
    const results: Array<Record<string, unknown>> = [];
    for (const code of codes) {
      const item = await this.callPythonLocal(code) as {
        code: string; name: string; score: number;
        patterns?: Array<{ direction: string; name: string }>;
      };
      const bullPatterns = this.normalizePatterns(item.patterns);
      if (onlyBull && bullPatterns.length === 0) continue;
      if (item.score < minScore) continue;
      results.push({
        code: item.code,
        name: item.name,
        score: item.score,
        patterns: this.normalizePatterns(item.patterns).map((p) => p.name),
      });
    }
    results.sort((a, b) => (b.score as number) - (a.score as number));
    return { items: results, count: results.length, run_mode: 'local' };
  }

  getSectors(): { items: Array<{ code: string; name: string; desc: string }>; run_mode: string } {
    return {
      items: [
        { code: 'AI', name: '人工智能', desc: 'AI大模型、算力、应用' },
        { code: 'NEW_ENERGY', name: '新能源', desc: '锂电、光伏、储能' },
        { code: 'SEMICONDUCTOR', name: '半导体', desc: '芯片、晶圆、封装' },
        { code: 'PHARMA', name: '医药生物', desc: '创新药、CXO、医疗器械' },
        { code: 'CONSUMPTION', name: '消费白酒', desc: '白酒、食品、零售' },
        { code: 'FINANCE', name: '大金融', desc: '银行、证券、保险' },
        { code: 'REAL_ESTATE', name: '房地产', desc: '地产、建材、家居' },
        { code: 'MILITARY', name: '军工航天', desc: '军工、航天、北斗' },
      ],
      run_mode: 'local',
    };
  }

  async scanSector(code: string): Promise<{
    items: Array<Record<string, unknown>>;
    count: number;
    sector: string;
    run_mode: string;
  }> {
    if (!this.pythonBinary) {
      throw new Error('无可用的Python分析后端');
    }
    const sampleCodes: Record<string, string[]> = {
      AI: ['300750', '002415', '600570', '300033', '002230'],
      NEW_ENERGY: ['300750', '002594', '600438', '002129', '300274'],
      SEMICONDUCTOR: ['603501', '002371', '688981', '300661', '688126'],
      PHARMA: ['600276', '300760', '002252', '603259', '002007'],
      CONSUMPTION: ['600519', '000858', '600887', '000333', '002415'],
      FINANCE: ['601398', '600036', '601318', '600030', '601628'],
      REAL_ESTATE: ['000002', '600048', '001979', '601668', '000725'],
      MILITARY: ['600760', '002025', '600893', '600118', '002179'],
    };
    const stockCodes = sampleCodes[code] || ['600519', '000001', '300750'];

    const results: Array<Record<string, unknown>> = [];
    for (const sc of stockCodes) {
      const item = await this.callPythonLocal(sc) as {
        code: string; name: string; score: number;
        patterns?: Array<{ name: string }>;
      };
      const normalized = this.normalizePatterns(item.patterns);
      results.push({
        code: item.code,
        name: item.name,
        score: item.score,
        patterns: normalized.slice(0, 2).map((p) => p.name),
      });
    }
    results.sort((a, b) => (b.score as number) - (a.score as number));
    return { items: results, count: results.length, sector: code, run_mode: 'local' };
  }

  getSyncConfig(): Record<string, unknown> {
    return {
      repo: 'github.com/user/kline-config',
      config_file: 'watchlist.json',
      branch: 'main',
      last_sync: '从未同步',
      status: 'local_only',
      run_mode: 'local',
    };
  }

  syncPull(): { error: string; detail: string; run_mode: string } {
    return {
      error: '本地模式下不支持GitHub同步',
      detail: '请配置 PYTHON_SERVICE_URL 启用远程同步功能',
      run_mode: 'local',
    };
  }
}
