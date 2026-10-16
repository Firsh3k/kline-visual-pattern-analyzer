import { Module } from '@nestjs/common';
import { StockAnalysisController } from './stock-analysis.controller';
import { StockAnalysisService } from './stock-analysis.service';
import { V4PageController } from './v4-page.controller';

@Module({
  controllers: [StockAnalysisController, V4PageController],
  providers: [StockAnalysisService],
})
export class StockAnalysisModule {}
