import { Module } from '@nestjs/common';

/**
 * ViewModule —— Web 视图层模块。
 *
 * 当前 V6 阶段：唯一的 Web 页面是 V4 深色终端（client/public/v4/index.html），
 * 它的根路径 / 与 /v4/* 由 server/main.ts 注册的 express 中间件托管（stock-analysis/v4-page.ts；
 * 平台不允许根级 @Controller），因此本模块暂不注册任何路由，仅作为 app.module 中的视图层占位，保证工程可从仓库独立构建
 * （此前该文件只存在于部署沙箱、未入库，导致仓库无法重建）。
 *
 * 后续 V5/V6 若接入模块化前端（多视图 / 静态资源 / SPA 深链兜底），
 * 统一在本模块注册，不再散落到业务模块。
 */
@Module({})
export class ViewModule {}
