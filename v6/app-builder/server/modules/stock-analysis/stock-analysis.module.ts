import { Module } from '@nestjs/common';
import { StockAnalysisController } from './stock-analysis.controller';
import { StockAnalysisService } from './stock-analysis.service';

// 根路径 / 与 /v4/* 的页面托管在 server/main.ts（express 中间件，见 v4-page.ts）：
// 平台要求 @Controller 路径以 api/ 或 openapi/ 开头，不能注册根级 Controller。
@Module({
  controllers: [StockAnalysisController],
  providers: [StockAnalysisService],
})
export class StockAnalysisModule {}
