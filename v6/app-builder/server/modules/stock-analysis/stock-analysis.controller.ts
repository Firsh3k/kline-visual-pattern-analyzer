import {
  Controller, Get, Post, Delete, Body, Param, Query,
  BadRequestException, NotFoundException, HttpException, ServiceUnavailableException,
} from '@nestjs/common';
import { assertAsOf } from './stock-analysis.pure';
import { StockAnalysisService } from './stock-analysis.service';

@Controller('api')
export class StockAnalysisController {
  constructor(private readonly service: StockAnalysisService) {}

  @Get('health')
  async health() {
    const pythonInfo = this.service.getPythonInfo();
    const coreStatus = this.service.getCoreStatus();
    const runMode = this.service.getRunMode();

    return {
      status: 'ok',
      architecture: 'NestJS + Python (形态识别 Source of Truth)',
      runMode,
      python_available: pythonInfo.available,
      python_version: pythonInfo.version,
      python_path: pythonInfo.path,
      core_installed: coreStatus.installed,
      core_analyzer: coreStatus.analyzer,
      core_source: coreStatus.source,
      core_install_error: coreStatus.installError,
      core_sha256: coreStatus.coreSha256,
      analyzer_sha256: coreStatus.analyzerSha256,
      engine_commit: coreStatus.engineCommit,
      watchlist_store: await this.service.checkWatchlist(),
      timestamp: new Date().toISOString(),
    };
  }

  @Get('analyze/:code')
  async analyze(@Param('code') code: string, @Query('asOf') asOf?: string) {
    if (!code || !/^\d{6}$/.test(code)) {
      throw new BadRequestException('股票代码必须为 6 位数字');
    }
    let checkedAsOf: string | undefined;
    try {
      checkedAsOf = assertAsOf(asOf);  // R67-3：未来 / 不存在日期在取行情前拒绝
    } catch (error) {
      throw new BadRequestException(error instanceof Error ? error.message : 'asOf 不合法');
    }
    try {
      return await this.service.analyzeStock(code, checkedAsOf);
    } catch (error) {
      throw new BadRequestException(
        error instanceof Error ? error.message : '分析失败'
      );
    }
  }

  @Get('market-env')
  async marketEnv() {
    return this.service.getMarketEnv();
  }

  /** FIR-41：自选股（服务端数据库，全站共用一份）。存储不可用 → 503，不在浏览器兜底。 */
  @Get('watchlist')
  getWatchlist() {
    return watchlistCall(() => this.service.getWatchlist());
  }

  @Delete('watchlist')
  clearWatchlist() {
    return watchlistCall(() => this.service.clearWatchlist());
  }

  /** 浏览器一次性迁入本机旧清单与删除记录：{ codes: string[], tombstones: [{code, deletedAt}] } */
  @Post('watchlist/import')
  importWatchlist(@Body() body: unknown) {
    return watchlistCall(() => this.service.importWatchlist(body));
  }

  @Post('watchlist/analyze')
  async analyzeWatchlist() {
    try {
      return await this.service.analyzeWatchlist();
    } catch (error) {
      throw new BadRequestException(
        error instanceof Error ? error.message : '批量分析失败'
      );
    }
  }

  @Post('watchlist/:code')
  addWatchlist(@Param('code') code: string) {
    return watchlistCall(() => this.service.addToWatchlist(code));
  }

  @Delete('watchlist/:code')
  removeWatchlist(@Param('code') code: string) {
    return watchlistCall(() => this.service.removeFromWatchlist(code));
  }

  @Post('scan')
  async scan(@Body() body: { codes: string[]; min_score?: number; only_bull?: boolean }) {
    if (!body.codes || !Array.isArray(body.codes) || body.codes.length === 0) {
      throw new BadRequestException('股票代码列表不能为空');
    }
    try {
      return await this.service.scanStocks(
        body.codes,
        body.min_score ?? 0,
        body.only_bull ?? false
      );
    } catch (error) {
      throw new BadRequestException(
        error instanceof Error ? error.message : '扫描失败'
      );
    }
  }

  /** FIR-41：提交扫描后台任务，立即返回 job_id；前端轮询 GET scan/jobs/:id */
  @Post('scan/jobs')
  createScanJob(@Body() body: { codes?: string[]; sectors?: string[]; min_score?: number; only_bull?: boolean }) {
    try {
      return this.service.createScanJob(body?.codes, {
        minScore: body?.min_score ?? 0,
        onlyBull: body?.only_bull ?? false,
      }, body?.sectors);
    } catch (error) {
      if ((error as { statusCode?: number })?.statusCode === 503) {
        throw new ServiceUnavailableException((error as Error).message);
      }
      throw new BadRequestException(error instanceof Error ? error.message : '提交扫描失败');
    }
  }

  @Get('scan/jobs/:id')
  getScanJob(@Param('id') id: string) {
    const job = this.service.getScanJob(id);
    if (!job) {
      throw new NotFoundException('扫描任务不存在或已过期（服务重启会清空任务），请重新扫描');
    }
    return job;
  }

  @Delete('scan/jobs/:id')
  cancelScanJob(@Param('id') id: string) {
    if (!this.service.cancelScanJob(id)) {
      throw new NotFoundException('扫描任务不存在或已过期');
    }
    return this.service.getScanJob(id);
  }

  /** FIR-41 第 4 项：扫描范围——板块清单、来源、数据日期、核对状态（不含成分明细） */
  @Get('sectors')
  sectors() {
    try {
      return this.service.listSectors();
    } catch (error) {
      throw new ServiceUnavailableException(error instanceof Error ? error.message : '板块清单不可用');
    }
  }

  @Post('sector/:code/scan')
  async sectorScan(@Param('code') code: string) {
    try {
      return await this.service.scanSector(code);
    } catch (error) {
      throw new BadRequestException(
        error instanceof Error ? error.message : '板块扫描失败'
      );
    }
  }

  @Get('sync/config')
  syncConfig() {
    return this.service.getSyncConfig();
  }

  @Post('sync/pull')
  syncPull() {
    const result = this.service.syncPull();
    throw new BadRequestException(result.error);
  }
}

/** 自选股接口统一错误映射：400 输入错误 / 503 存储不可用 / 其它 500 */
async function watchlistCall<T>(fn: () => Promise<T>): Promise<T> {
  try {
    return await fn();
  } catch (error) {
    const status = (error as any)?.statusCode;
    const msg = error instanceof Error ? error.message : String(error);
    if (status === 400) throw new BadRequestException(msg);
    if (status === 503) throw new ServiceUnavailableException(msg);
    throw new HttpException(`自选股存储出错：${msg}`, 500);
  }
}
