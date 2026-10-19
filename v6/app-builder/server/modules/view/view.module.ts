import { Module } from '@nestjs/common';

/**
 * ViewModule —— Web 视图层模块。
 *
 * 当前 V6 阶段：唯一的 Web 页面是 V4 深色终端（client/public/v4/index.html），
 * 它的根路径托管由 StockAnalysisModule 中的 V4PageController 负责，因此本模块
 * 暂不注册任何路由，仅作为 app.module 中的视图层占位，保证工程可从仓库独立构建
 * （此前该文件只存在于部署沙箱、未入库，导致仓库无法重建）。
 *
 * 后续 V5/V6 若接入模块化前端（多视图 / 静态资源 / SPA 深链兜底），
 * 统一在本模块注册，不再散落到业务模块。
 */
@Module({})
export class ViewModule {}
