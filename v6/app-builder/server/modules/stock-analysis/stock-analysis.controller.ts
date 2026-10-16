import {
  Controller, Get, Post, Delete, Body, Param,
  BadRequestException, ConflictException, NotFoundException,
} from '@nestjs/common';
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
      watchlistCount: this.service.getWatchlist().count,
      timestamp: new Date().toISOString(),
    };
  }

  @Get('analyze/:code')
  async analyze(@Param('code') code: string) {
    if (!code || code.length < 6) {
      throw new BadRequestException('股票代码无效');
    }
    try {
      return await this.service.analyzeStock(code);
    } catch (error) {
      throw new BadRequestException(
        error instanceof Error ? error.message : '分析失败'
      );
    }
  }

  @Get('market-env')
  marketEnv() {
    return this.service.getMarketEnv();
  }

  @Get('watchlist')
  getWatchlist() {
    return this.service.getWatchlist();
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
    try {
      return this.service.addToWatchlist(code);
    } catch (error) {
      if (error instanceof Error && (error as any).statusCode === 409) {
        throw new ConflictException(error.message);
      }
      throw error;
    }
  }

  @Delete('watchlist/:code')
  removeWatchlist(@Param('code') code: string) {
    try {
      return this.service.removeFromWatchlist(code);
    } catch (error) {
      if (error instanceof Error && (error as any).statusCode === 404) {
        throw new NotFoundException(error.message);
      }
      throw error;
    }
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

  @Get('sectors')
  sectors() {
    return this.service.getSectors();
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
