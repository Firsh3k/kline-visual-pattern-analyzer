/**
 * FIR-47 整改④：真实 Nest 端到端测试的【最小启动器】。
 *
 * 只装载 StockAnalysisModule（真实 StockAnalysisController → StockAnalysisService → EngineResolver
 * → Python subprocess → Canonical Core），不装载 PlatformModule，因此没有平台 CSRF/鉴权/页面中间件，
 * /api/analyze/:code 与 /api/health 走与生产完全相同的控制器/服务/引擎代码路径，仅省略与分析信号无关的
 * 平台 HTTP 外壳。固定行情由 KLINE_FIXTURE_DIR 测试接缝注入（生产不设此 env）。
 *
 * 由 test_us_nest_e2e.js spawn；需先 nest build 产出 dist/server。
 */
'use strict';
const path = require('path');
const APP = path.resolve(__dirname, '..', '..', 'v6', 'app-builder');
require(path.join(APP, 'node_modules', 'reflect-metadata'));
const { NestFactory } = require(path.join(APP, 'node_modules', '@nestjs', 'core'));
const { StockAnalysisModule } = require(path.join(APP, 'dist', 'server', 'modules', 'stock-analysis', 'stock-analysis.module.js'));

(async () => {
  const app = await NestFactory.create(StockAnalysisModule, { logger: false, abortOnError: false });
  const port = Number(process.env.SERVER_PORT || '3000');
  await app.listen(port, '127.0.0.1');
  console.log('US-E2E-READY ' + port);
})().catch(e => { console.error('US-E2E-BOOT-FAIL', e); process.exit(1); });