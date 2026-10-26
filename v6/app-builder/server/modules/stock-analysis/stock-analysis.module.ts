import { Module } from '@nestjs/common';
import { StockAnalysisController } from './stock-analysis.controller';
import { StockAnalysisService } from './stock-analysis.service';

// 根路径 / 与 /v4/* 的 V4 页面托管已移至 server/main.ts 的 express 全局中间件，
// 因为平台 lint 要求 @Controller 路径必须以 api/ 或 openapi/ 开头，不能注册根级 Controller。
@Module({
  controllers: [StockAnalysisController],
  providers: [StockAnalysisService],
})
export class StockAnalysisModule {}
